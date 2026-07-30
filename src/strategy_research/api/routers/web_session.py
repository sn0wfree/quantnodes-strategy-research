"""Web session API — create/list/update/delete + messages + FTS5 search.

Sessions and messages are persisted to SQLite (same DB as users).

Schema:
  sessions:       id, user_id, title, created_at, updated_at,
                  starred, tags_json, message_count, archived
  messages:       id, session_id (FK CASCADE), role, content,
                  parts_json, created_at, metadata_json
  messages_fts:   FTS5 virtual table over messages(content, role)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSessionCreate(BaseModel):
    title: str = "新会话"


class WebSessionUpdate(BaseModel):
    title: Optional[str] = None
    starred: Optional[bool] = None
    archived: Optional[bool] = None
    tags: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_db_path() -> Path:
    db_dir = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    return db_dir / "quantnodes_strategy_research_user.db"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables + indexes + FTS5 triggers (idempotent)."""
    # Main sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Backfill new columns for pre-existing databases (idempotent via try/except)
    _add_column(conn, "sessions", "starred", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "sessions", "message_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "sessions", "archived", "INTEGER NOT NULL DEFAULT 0")

    # Messages table (FK cascade)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            parts_json TEXT,
            tool_call_id TEXT,
            created_at REAL NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_created "
        "ON messages(session_id, created_at)"
    )
    _add_column(conn, "messages", "tool_call_id", "TEXT")

    # Attempts table — tracks each AgentLoop execution (借鉴 vibe_trading)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_attempt_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            prompt TEXT,
            run_dir TEXT,
            summary TEXT,
            react_trace_json TEXT,
            metrics_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            message_id TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_attempts_session "
        "ON attempts(session_id, created_at)"
    )

    # FTS5 virtual table (graceful fallback if FTS5 not compiled in).
    # Use trigram tokenizer for substring matching across Chinese/English boundaries
    # (unicode61 default doesn't split Latin/Chinese tokens).
    #
    # Auto-heal: if messages_fts already exists with the wrong tokenizer
    # (e.g. unicode61 from an older schema version), drop it and recreate
    # with trigram so search behaves consistently. Otherwise SQLite happily
    # skips the CREATE VIRTUAL TABLE IF NOT EXISTS and the wrong tokenizer
    # sticks forever.
    needs_fts_rebuild = False
    try:
        existing_fts = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        if existing_fts and existing_fts[0]:
            sql_text = existing_fts[0].lower()
            if "tokenize='trigram'" not in sql_text and 'tokenize="trigram"' not in sql_text:
                needs_fts_rebuild = True
                logger.warning(
                    "messages_fts exists with wrong tokenizer; "
                    "auto-rebuilding with trigram tokenizer..."
                )

        if needs_fts_rebuild:
            # Drop the triggers first so we can rebuild cleanly
            for trig in ("messages_ai", "messages_ad", "messages_au"):
                try:
                    conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
                except sqlite3.OperationalError as exc:
                    logger.warning("drop trigger %s: %s", trig, exc)
            try:
                conn.execute("DROP TABLE IF EXISTS messages_fts")
            except sqlite3.OperationalError as exc:
                logger.error(
                    "drop messages_fts failed (%s); skipping FTS rebuild", exc
                )
                needs_fts_rebuild = False

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                role,
                content='messages',
                content_rowid='rowid',
                tokenize='trigram'
            )
        """)
        # Triggers to keep FTS in sync with messages
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content, role)
                VALUES (new.rowid, new.content, new.role);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content, role)
                VALUES ('delete', old.rowid, old.content, old.role);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content, role)
                VALUES ('delete', old.rowid, old.content, old.role);
                INSERT INTO messages_fts(rowid, content, role)
                VALUES (new.rowid, new.content, new.role);
            END
        """)

        # If we rebuilt the FTS table, repopulate it from messages
        if needs_fts_rebuild:
            try:
                conn.execute(
                    "INSERT INTO messages_fts(rowid, content, role) "
                    "SELECT rowid, content, role FROM messages"
                )
                logger.info(
                    "messages_fts rebuilt with trigram tokenizer; "
                    "backfilled from messages table"
                )
            except sqlite3.OperationalError as exc:
                logger.error("FTS backfill failed (continuing): %s", exc)
    except sqlite3.OperationalError as exc:
        logger.warning("FTS5 unavailable, search endpoint will be disabled: %s", exc)


def _add_column(conn: sqlite3.Connection, table: str, column: str, spec: str) -> None:
    """Add a column if it doesn't exist (SQLite has no IF NOT EXISTS for columns)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    except sqlite3.OperationalError:
        # Column already exists — expected on second run
        pass


def _get_db() -> sqlite3.Connection:
    """Open the shared SQLite connection. Ensures schema is up to date."""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    conn.commit()
    return conn


def _row_to_session(row: sqlite3.Row) -> dict:
    """Convert a sessions row to API JSON shape."""
    tags_raw = row["tags_json"] or "[]"
    try:
        tags = json.loads(tags_raw)
    except (json.JSONDecodeError, TypeError):
        tags = []
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "starred": bool(row["starred"]),
        "tags": tags,
        "message_count": row["message_count"],
        "archived": bool(row["archived"]),
    }


def _row_to_message(row: sqlite3.Row) -> dict:
    """Convert a messages row to API JSON shape."""
    parts: list[Any] = []
    if row["parts_json"]:
        try:
            parsed = json.loads(row["parts_json"])
            parts = parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            parts = []
    metadata = None
    if row["metadata_json"]:
        try:
            metadata = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            metadata = None
    # For user messages without explicit parts, build a single text part
    # from content so the frontend doesn't need to handle null parts.
    if not parts and row["role"] == "user" and row["content"]:
        parts = [{"type": "text", "text": row["content"]}]
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "parts": parts,
        "tool_call_id": row["tool_call_id"] if "tool_call_id" in row.keys() else None,
        "created_at": row["created_at"],
        "metadata": metadata,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public functions used by chat.py
# ─────────────────────────────────────────────────────────────────────────────


def persist_message(
    session_id: str,
    role: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
    metadata: Optional[dict[str, Any]] = None,
    message_id: Optional[str] = None,
    created_at: Optional[float] = None,
    tool_call_id: Optional[str] = None,
) -> str:
    """Insert a message and bump session counters. Returns message id.

    Safe to call from background tasks — does not raise on failure (logs only).
    """
    msg_id = message_id or str(uuid.uuid4())
    ts = created_at or time.time()
    parts_json = json.dumps(parts, ensure_ascii=False) if parts is not None else None
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            logger.warning("persist_message: session %s not found", session_id)
            return msg_id
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, parts_json, tool_call_id, ts, metadata_json),
        )
        conn.execute(
            "UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("persist_message failed for session %s: %s", session_id, exc, exc_info=True)
    return msg_id


def auto_title_session(session_id: str, content: str, max_len: int = 30) -> Optional[str]:
    """Set session title from first user message if title is still default.

    Returns the new title if changed, None otherwise.
    """
    if not content or not content.strip():
        return None
    title = content.strip().replace("\n", " ")
    if len(title) > max_len:
        title = title[:max_len].rstrip() + "…"
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT title, message_count FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        if row["title"] != "新会话":
            return None
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), session_id),
        )
        conn.commit()
        return title
    except Exception as exc:
        logger.error("auto_title_session failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Session CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("")
async def create_session(body: WebSessionCreate, request: Request):
    """Create a new web session."""
    user_id = getattr(request.state, "user_id", "anonymous")
    session_id = str(uuid.uuid4())
    now = time.time()
    conn = _get_db()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
        "starred, tags_json, message_count, archived) "
        "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
        (session_id, user_id, body.title, now, now),
    )
    conn.commit()
    return {
        "id": session_id,
        "user_id": user_id,
        "title": body.title,
        "created_at": now,
        "updated_at": now,
        "starred": False,
        "tags": [],
        "message_count": 0,
        "archived": False,
    }


@router.get("")
async def list_sessions(request: Request, limit: int = 50, include_archived: bool = False):
    """List sessions for current user, most recent first."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    sql = (
        "SELECT * FROM sessions WHERE user_id = ? "
        + ("" if include_archived else "AND archived = 0 ")
        + "ORDER BY updated_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, (user_id, limit)).fetchall()
    return {"sessions": [_row_to_session(r) for r in rows]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get a single session."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return _row_to_session(row)


@router.patch("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session metadata: title, starred, archived, tags."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    now = time.time()
    fields: dict[str, Any] = {"updated_at": now}
    if body.title is not None:
        fields["title"] = body.title
    if body.starred is not None:
        fields["starred"] = 1 if body.starred else 0
    if body.archived is not None:
        fields["archived"] = 1 if body.archived else 0
    if body.tags is not None:
        fields["tags_json"] = json.dumps(body.tags, ensure_ascii=False)

    if len(fields) > 1:  # has at least one user-supplied change
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [session_id]
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", params)
        conn.commit()

    # Re-read
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row)


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a session. CASCADE removes its messages and FTS rows."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    # messages FK CASCADE handles the delete; explicit delete also fine
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return {"status": "ok", "deleted_id": session_id}


def delete_messages(session_id: str, message_ids: list[str]) -> None:
    """Delete specific messages by ID from a session."""
    if not message_ids:
        return
    try:
        conn = _get_db()
        placeholders = ",".join("?" for _ in message_ids)
        conn.execute(
            f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
            [session_id, *message_ids],
        )
        conn.execute(
            "UPDATE sessions SET message_count = message_count - ?, updated_at = ? WHERE id = ?",
            (len(message_ids), time.time(), session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("delete_messages failed for session %s: %s", session_id, exc)


def update_message_content(
    message_id: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Update a message's content and optionally its parts."""
    try:
        conn = _get_db()
        parts_json = json.dumps(parts, ensure_ascii=False) if parts is not None else None
        if parts is not None:
            conn.execute(
                "UPDATE messages SET content = ?, parts_json = ? WHERE id = ?",
                (content, parts_json, message_id),
            )
        else:
            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, message_id),
            )
        conn.commit()
    except Exception as exc:
        logger.error("update_message_content failed for message %s: %s", message_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Messages endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 200,
    before: Optional[float] = None,
):
    """List messages for a session, oldest first.

    Optional ``before`` cursor (unix ts) for pagination. ``has_more=True`` if
    there are additional older messages beyond ``limit``.
    """
    conn = _get_db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    params: list[Any] = [session_id]
    cursor_sql = ""
    if before is not None:
        cursor_sql = "AND created_at < ?"
        params.append(before)
    params.append(limit + 1)  # +1 to detect has_more
    rows = conn.execute(
        f"SELECT * FROM messages WHERE session_id = ? {cursor_sql} "
        "ORDER BY created_at ASC LIMIT ?",
        params,
    ).fetchall()
    has_more = len(rows) > limit
    messages = [_row_to_message(r) for r in rows[:limit]]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    return {"messages": messages, "has_more": has_more, "total": total}


# ─────────────────────────────────────────────────────────────────────────────
# FTS5 search endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _make_snippet(content: str, query: str, max_len: int = 120) -> str:
    """Extract a snippet around the first match, escaping HTML and wrapping matches in <mark>."""
    import html

    if not content:
        return ""
    if not query:
        return html.escape(content[:max_len])
    # Find first occurrence of any query token (whitespace-split)
    tokens = [t for t in query.split() if t]
    lower = content.lower()
    first_pos = -1
    for tok in tokens:
        pos = lower.find(tok.lower())
        if pos != -1 and (first_pos == -1 or pos < first_pos):
            first_pos = pos
    if first_pos == -1:
        snippet = content[:max_len]
    else:
        # Window around the match
        start = max(0, first_pos - 30)
        end = min(len(content), first_pos + max_len - 30)
        snippet = content[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"
    escaped = html.escape(snippet)
    # Highlight tokens (case-insensitive)
    for tok in tokens:
        if not tok:
            continue
        pattern = html.escape(tok)
        # Use simple find/replace with case-insensitive matching via lowered scan
        out_parts = []
        cursor = 0
        low = escaped.lower()
        while True:
            idx = low.find(pattern.lower(), cursor)
            if idx == -1:
                out_parts.append(escaped[cursor:])
                break
            out_parts.append(escaped[cursor:idx])
            out_parts.append(f"<mark>{escaped[idx:idx + len(pattern)]}</mark>")
            cursor = idx + len(pattern)
        escaped = "".join(out_parts)
    return escaped


@router.post("/search")
async def search_sessions(body: SearchRequest, request: Request):
    """Full-text search across all messages.

    Returns hits with session metadata + highlighted snippet. Joins with
    sessions table to filter by current user_id.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    # Detect if FTS5 is available
    has_fts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone() is not None
    if not has_fts:
        raise HTTPException(status_code=503, detail="Full-text search is unavailable on this build")
    if not body.query or not body.query.strip():
        return {"hits": [], "query": body.query}

    # Build FTS5 MATCH expression. For multi-word queries, AND each token
    # (so "alpha 策略" → "alpha AND 策略"). For single word, use as-is.
    tokens = [t for t in body.query.split() if t]
    # Strip FTS5 reserved chars from each token to avoid syntax errors
    safe_tokens = [t.replace('"', '').replace("'", '').replace('(', '').replace(')', '') for t in tokens]
    if not safe_tokens:
        return {"hits": [], "query": body.query}
    if len(safe_tokens) == 1:
        match_expr = safe_tokens[0]
    else:
        match_expr = " AND ".join(f'"{tok}"' for tok in safe_tokens if tok)

    rows = conn.execute(
        """
        SELECT
            m.id AS message_id,
            m.session_id,
            m.role,
            m.content,
            m.created_at,
            s.title AS session_title,
            fts.rank AS score
        FROM messages_fts fts
        JOIN messages m ON m.rowid = fts.rowid
        JOIN sessions s ON s.id = m.session_id
        WHERE messages_fts MATCH ?
          AND s.user_id = ?
          AND s.archived = 0
        ORDER BY fts.rank
        LIMIT ?
        """,
        (match_expr, user_id, body.limit),
    ).fetchall()

    hits = []
    for r in rows:
        hits.append(
            {
                "session_id": r["session_id"],
                "session_title": r["session_title"],
                "message_id": r["message_id"],
                "role": r["role"],
                "snippet": _make_snippet(r["content"], body.query),
                "score": float(r["score"]) if r["score"] is not None else 0.0,
                "created_at": r["created_at"],
            }
        )
    return {"hits": hits, "query": body.query}