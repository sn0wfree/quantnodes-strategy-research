"""Web session API — create/list/update/delete for Web UI sessions。"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# In-memory store (will be replaced by SQLite later)
_sessions: dict[str, dict] = {}


class WebSessionCreate(BaseModel):
    title: str = "New Session"


class WebSessionUpdate(BaseModel):
    title: Optional[str] = None


@router.post("")
async def create_session(body: WebSessionCreate, request: Request):
    """Create a new web session."""
    user_id = getattr(request.state, "user_id", "anonymous")
    session_id = str(uuid.uuid4())
    now = time.time()
    _sessions[session_id] = {
        "id": session_id,
        "user_id": user_id,
        "title": body.title,
        "created_at": now,
        "updated_at": now,
    }
    return _sessions[session_id]


@router.get("")
async def list_sessions(request: Request, limit: int = 50):
    """List sessions for current user."""
    user_id = getattr(request.state, "user_id", "anonymous")
    sessions = [
        s for s in _sessions.values()
        if s["user_id"] == user_id
    ]
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return {"sessions": sessions[:limit]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get a single session."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.put("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session title."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.title is not None:
        session["title"] = body.title
    session["updated_at"] = time.time()
    return session


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a session."""
    if session_id in _sessions:
        del _sessions[session_id]
    return {"status": "ok"}
