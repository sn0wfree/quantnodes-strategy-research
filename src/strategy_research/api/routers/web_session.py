"""Web session API — create/list/update/delete for Web UI sessions。

Sessions are persisted to SQLite (same DB as users).
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class WebSessionCreate(BaseModel):
    title: str = "New Session"


class WebSessionUpdate(BaseModel):
    title: Optional[str] = None


def _get_db():
    """Get the shared SQLite connection for sessions."""
    import sqlite3
    from pathlib import Path
    import os

    db_dir = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    db_path = db_dir / "quantnodes_strategy_research_user.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New Session',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


@router.post("")
async def create_session(body: WebSessionCreate, request: Request):
    """Create a new web session."""
    user_id = getattr(request.state, "user_id", "anonymous")
    session_id = str(uuid.uuid4())
    now = time.time()
    conn = _get_db()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, user_id, body.title, now, now),
    )
    conn.commit()
    return {"id": session_id, "title": body.title, "created_at": now, "updated_at": now}


@router.get("")
async def list_sessions(request: Request, limit: int = 50):
    """List sessions for current user, most recent first."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return {"sessions": [dict(r) for r in rows]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get a single session."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return dict(row)


@router.put("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session title."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    now = time.time()
    title = body.title if body.title is not None else row["title"]
    conn.execute(
        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
        (title, now, session_id),
    )
    conn.commit()
    return {"id": session_id, "title": title, "created_at": row["created_at"], "updated_at": now}


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a session."""
    conn = _get_db()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return {"status": "ok"}
