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
    # Enable foreign key constraints (Level 2, Phase 2 commit 2).
    # Required for the new message_parts.session_id / .message_id
    # FOREIGN KEY ... ON DELETE CASCADE to actually fire. The existing
    # messages.session_id FK has been declared since the original
    # schema, but was inert without this PRAGMA — see comments on
    # the message_parts table below.
    conn.execute("PRAGMA foreign_keys=ON")
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

    # Per-session monotonic sequence number (Level 1, opencode-aligned).
    # Default 0 keeps existing rows valid; backfill script assigns real
    # values from created_at order. The UNIQUE INDEX is created in
    # scripts/backfill_seq.py after backfill to avoid constraint
    # conflicts on legacy rows.
    _add_column(conn, "messages", "seq", "INTEGER NOT NULL DEFAULT 0")
    # No index here — backfill creates UNIQUE INDEX uq_messages_session_seq
    # which is strictly stronger than a non-unique index on the same cols.

    # message_parts (Level 2, opencode-aligned, Phase 2 commit 1)
    # Splits parts out of the messages.parts_json JSON column into a
    # dedicated table. Each part is a row; the parent message_id links
    # back. opencode's PartTable model.
    #
    # Fields:
    #   id          : stable per-part UUID (independent of message_id)
    #   message_id  : FK to messages.id (CASCADE delete)
    #   session_id  : FK to sessions.id (CASCADE delete) — denormalized
    #                 so list_messages can fetch all parts in one JOIN
    #   type        : part type (text | tool_call | tool_result | thinking
    #                 | file_edit | table | chart | image)
    #   data_json   : full part data as JSON (preserves opencode shape)
    #   seq         : part ordering within the message (Level 1 invariant)
    #   time_created: when this part was created (vs message.created_at
    #                 which is when the message row was inserted)
    #
    # No reads/writes yet — commit 2 (dual-write) wires the writes,
    # commit 3 (migration) populates from existing parts_json, commit
    # 5 switches the read path. Until commit 5, this table is empty
    # and the old parts_json column is the source of truth.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_parts_message_seq "
        "ON message_parts(message_id, seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_parts_session_seq "
        "ON message_parts(session_id, seq)"
    )

    # Compaction message type (opencode-aligned, fixes "spontaneous summary" bug)
    # Use nullable column first, then UPDATE existing rows, to avoid NOT NULL failure
    _add_column(conn, "messages", "message_type", "TEXT")
    conn.execute(
        "UPDATE messages SET message_type = 'assistant' WHERE message_type IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_type_created "
        "ON messages(session_id, message_type, created_at)"
    )

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


def _migrate_text_part_ids(conn: sqlite3.Connection) -> None:
    """Assign deterministic ids to text parts missing one (text-part-routing).

    After the opencode-style 3-step text protocol (PR1), every text part
    in `parts_json` must carry an `id` field so the frontend can route
    streaming deltas to the correct segment via findLast by id.

    This migration scans all messages and assigns ``f"legacy-{msg_id}-{idx}"``
    to any text part without an id. Idempotent: parts that already have an
    id are left untouched. Failures are logged and skipped so a single
    corrupt row doesn't abort the whole migration.
    """
    rows = conn.execute(
        "SELECT id, parts_json FROM messages WHERE parts_json IS NOT NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        msg_id = row["id"]
        try:
            parts = json.loads(row["parts_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("migration: msg %s parse failed: %s", msg_id, exc)
            continue
        if not isinstance(parts, list):
            continue
        changed = False
        for i, p in enumerate(parts):
            if (
                isinstance(p, dict)
                and p.get("type") == "text"
                and not p.get("id")
            ):
                p["id"] = f"legacy-{msg_id}-{i}"
                changed = True
        if changed:
            try:
                conn.execute(
                    "UPDATE messages SET parts_json = ? WHERE id = ?",
                    (json.dumps(parts, ensure_ascii=False), msg_id),
                )
                updated += 1
            except sqlite3.Error as exc:
                logger.warning("migration: msg %s update failed: %s", msg_id, exc)
    if updated:
        logger.info(
            "text-part-routing migration: assigned ids to %d legacy text parts",
            updated,
        )


def _migrate_message_types(conn: sqlite3.Connection) -> None:
    """Mark historical compaction messages.

    Part of the opencode-aligned compaction fix. Old assistant messages
    with [context summary] prefix, ## Anchored Summary structure, or
    LLM-generated summary format (## Objective...) are L4-compaction
    artifacts, NOT regular assistant turns. Without the
    message_type='compaction' flag, the LLM treats them as previous
    turns and continues the summary task on the next user message —
    producing "spontaneous summaries".

    Idempotent: only updates rows where message_type='assistant' (the
    default), so re-running the migration is a no-op.
    """
    updated = conn.execute("""
        UPDATE messages
        SET message_type = 'compaction'
        WHERE message_type = 'assistant'
          AND (
              content LIKE '[context summary]%'
              OR content LIKE '## Anchored Summary%'
              OR content LIKE '## Objective%'
              OR content LIKE '%## Important Details%'
          )
    """).rowcount
    if updated:
        logger.info(
            "compaction migration: marked %d historical messages as compaction",
            updated,
        )


def _get_db() -> sqlite3.Connection:
    """Open the shared SQLite connection. Ensures schema is up to date."""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    # Idempotent migration: ensure every text part has an id (text-part-routing)
    _migrate_text_part_ids(conn)
    # Idempotent migration: mark historical compaction messages (opencode-aligned)
    _migrate_message_types(conn)
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


def _row_to_message(
    row: sqlite3.Row,
    parts: list[Any] | None = None,
) -> dict:
    """Convert a messages row to API JSON shape.

    Args:
        row: sqlite3.Row from a `SELECT * FROM messages ...` query.
        parts: Optional pre-fetched parts (Level 2, Phase 2). If None,
            falls back to `parts_json` column on the row. Callers
            should pass parts explicitly to avoid the N+1 problem
            and to support the new message_parts table (commit 5).

    Reads parts from:
    1. The `parts` parameter (preferred; batch-fetched by caller)
    2. The legacy `parts_json` column (fallback for pre-migration
       rows and test fixtures)
    """
    if parts is None:
        # Fallback: legacy parts_json column
        parts = []
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
    # Same for error messages (friendly text in content → text part).
    if not parts and row["content"]:
        if row["role"] == "user" or row["message_type"] == "error":
            parts = [{"type": "text", "text": row["content"]}]
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "parts": parts,
        "tool_call_id": row["tool_call_id"] if "tool_call_id" in row.keys() else None,
        "created_at": row["created_at"],
        "seq": row["seq"] if "seq" in row.keys() else 0,
        "metadata": metadata,
        "message_type": row["message_type"] if "message_type" in row.keys() else "assistant",
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
    message_type: str = "assistant",
    seq: int | None = None,
) -> str:
    """Insert a message and bump session counters. Returns message id.

    Safe to call from background tasks — does not raise on failure (logs only).

    Args:
        message_type: One of 'user' | 'assistant' | 'tool' | 'compaction' | 'error'.
            Defaults to 'assistant' for backward compat.
        seq: Per-session monotonic sequence number (Level 1, opencode-aligned).
            If None, falls back to 0 (the column default). Callers SHOULD
            pass an explicit seq via the SeqGenerator for new messages.
    """
    logger.debug("[DB] persist_message session=%s role=%s type=%s content_len=%d seq=%s",
                session_id, role, message_type, len(content), seq)
    msg_id = message_id or str(uuid.uuid4())
    ts = created_at or time.time()
    parts_json = json.dumps(parts, ensure_ascii=False) if parts is not None else None
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            logger.warning("[DB] persist_message: session %s not found", session_id)
            return msg_id
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json, message_type, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, parts_json, tool_call_id, ts, metadata_json, message_type, seq or 0),
        )
        logger.debug("[DB] persisted id=%s", msg_id)

        # Dual-write: also insert parts into message_parts (Level 2, Phase 2
        # commit 2). The old parts_json column remains the source of truth
        # until commit 5 switches the read path. This commit only adds
        # the parallel write so commits 3-4 (migration + read switch) can
        # proceed without losing data.
        if parts:
            for i, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                part_id = str(uuid.uuid4())
                part_type = p.get("type", "text")
                part_data_json = json.dumps(p, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO message_parts "
                    "(id, message_id, session_id, type, data_json, seq, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (part_id, msg_id, session_id, part_type, part_data_json, i, ts),
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

    Order key: ``seq`` (Level 1, opencode-aligned). Per-session monotonic
    counter that's invariant under clock skew. ``before`` continues to
    accept a unix ts; we map it to the maximum seq seen before that ts
    via a subquery for cursor-pagination compatibility.
    """
    conn = _get_db()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    params: list[Any] = [session_id]
    cursor_sql = ""
    if before is not None:
        # Convert unix-ts cursor into a seq cursor by finding the
        # smallest seq with created_at > before. This preserves the
        # external API while using seq internally for ordering.
        cursor_sql = (
            "AND seq > COALESCE("
            "(SELECT MIN(seq) - 1 FROM messages "
            "WHERE session_id = ? AND created_at >= ?), 0)"
        )
        params.extend([session_id, before])
    params.append(limit + 1)  # +1 to detect has_more
    rows = conn.execute(
        f"SELECT * FROM messages WHERE session_id = ? {cursor_sql} "
        "ORDER BY seq ASC LIMIT ?",
        params,
    ).fetchall()
    has_more = len(rows) > limit

    # Batch-fetch parts for all messages in one query (avoids N+1).
    # Level 2 / Phase 2 commit 4: parts are now in the message_parts
    # table. Read from there; fall back to parts_json only if no
    # message_parts rows exist (pre-migration rows).
    if rows:
        message_ids = [r["id"] for r in rows[:limit]]
        placeholders = ",".join("?" * len(message_ids))
        parts_rows = conn.execute(
            f"SELECT message_id, data_json FROM message_parts "
            f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
            message_ids,
        ).fetchall()
        # Group by message_id, preserving seq order. Only include
        # messages that have at least one message_parts row; missing
        # keys (via .get) signal to _row_to_message that the caller
        # didn't pre-fetch (so it falls back to parts_json).
        parts_by_msg: dict[str, list[Any]] = {}
        for mid, data_json in parts_rows:
            try:
                part = json.loads(data_json)
                if isinstance(part, dict):
                    parts_by_msg.setdefault(mid, []).append(part)
            except (json.JSONDecodeError, TypeError):
                pass
    else:
        parts_by_msg = {}
    messages = [
        _row_to_message(r, parts=parts_by_msg.get(r["id"]))
        for r in rows[:limit]
    ]
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