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
import threading
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
    """Unified session DB path (delegates to resolve_session_db_path).

    Both web_session and EventStore route through the same resolver so
    they can never point at different files. See
    ``core.agent.memory_manager.resolve_session_db_path`` for the
    resolution order (SR_SESSIONS_DB > SR_WORKSPACE_PATH > cwd >
    ~/.quantnodes fallback).
    """
    from ...core.agent.memory_manager import resolve_session_db_path
    return resolve_session_db_path()


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

    # Messages table (FK cascade).
    #
    # Note: parts_json and tool_call_id columns were dropped in
    # Level 2 / Phase 2 commit 6 — see cleanup block below. The schema
    # here reflects the post-cleanup state. For pre-commit-6 databases,
    # the drop happens via the cleanup block; for fresh databases,
    # the table is created with the new schema directly.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session_created "
        "ON messages(session_id, created_at)"
    )

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

    # ── One-time destructive/expensive migrations (versioned) ────────
    # Run once per database via PRAGMA user_version, not on every
    # connection open. Previously the DELETE below executed on every
    # _get_db() call (O(n) per write + any stray role='tool' rows — e.g.
    # written by the B4 transition — were destroyed at runtime). The
    # migration script (scripts/migrate_role_tool_to_assistant.py) must
    # have been run BEFORE this code on existing DBs, otherwise
    # result-bearing tool results are lost (same caveat, now only once).
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        # Backfill text-part ids while parts_json still exists
        _migrate_text_part_ids(conn)
        conn.execute("DELETE FROM messages WHERE role = 'tool'")
        if _has_column(conn, "messages", "tool_call_id"):
            _drop_column(conn, "messages", "tool_call_id")
        if _has_column(conn, "messages", "parts_json"):
            _drop_column(conn, "messages", "parts_json")
        conn.execute("PRAGMA user_version = 2")
    if version < 3:
        # One-time compaction-type backfill (full-table LIKE scan)
        _migrate_message_types(conn)
        conn.execute("PRAGMA user_version = 3")

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

    # event_log (Level 3, Phase 3 B1 commit 1) — append-only event store.
    #
    # Every AgentLoop event (message_received, text.started, text_delta,
    # text.ended, tool.call, tool.result, llm_usage, agent_done, ...) is
    # persisted here as an immutable row. The projector reads from this
    # log to update messages + message_parts; the EventBus reads from
    # here to replay events for SSE reconnect.
    #
    # Schema (opencode-aligned):
    #   id           — stable per-event UUID
    #   aggregate_id — session_id (the aggregate root)
    #   seq          — per-aggregate monotonic integer (UNIQUE)
    #   type         — event type string (dot-namespaced: "text.started")
    #   data_json    — event payload as JSON (preserves opencode shape)
    #   time_created — wall-clock timestamp (server time.time())
    #
    # The (aggregate_id, seq) UNIQUE INDEX ensures events are append-only
    # and provides efficient replay (WHERE aggregate_id = ? ORDER BY seq).
    # The (type, time_created) index supports time-range queries by event
    # type (e.g. "all tool_result events in the last hour").
    #
    # Phase 3 B1: this table is created empty and unused. Service code
    # still writes directly to messages + message_parts. Phase 3 B2 will
    # add EventBusV2 dual-write; B3 will switch the read path to read
    # from the projector (which materializes from event_log).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
            UNIQUE (aggregate_id, seq)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_seq "
        "ON event_log(aggregate_id, seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_log_type_time "
        "ON event_log(type, time_created)"
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


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True if the given table has the given column."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _drop_column(conn: sqlite3.Connection, table: str, column: str) -> None:
    """Drop a column from a table.

    SQLite has no DROP COLUMN directly until 3.35.0. For broad
    compatibility we use the 12-step "rename-copy-drop" recipe:
    1. Create new_<table> with desired schema (no dropped column)
    2. Copy data from old → new
    3. Drop old table
    4. Rename new → old
    5. Recreate indexes, triggers, foreign keys

    This is heavy but correct on any SQLite version.
    """
    if not _has_column(conn, table, column):
        return  # already dropped

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    keep_cols = [r[1] for r in rows if r[1] != column]
    keep_cols_csv = ",".join(keep_cols)

    new_table = f"_new_{table}"
    # Build CREATE TABLE statement from existing schema
    schema_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    if not schema_rows:
        return
    original_sql = schema_rows[0][0]

    # Extract the actual CREATE TABLE line (may be quoted or unquoted).
    import re
    create_match = re.match(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?[\w]+\"?)",
        original_sql,
        re.IGNORECASE,
    )
    if not create_match:
        return
    table_ref = create_match.group(1)
    quoted = table_ref.startswith('"') and table_ref.endswith('"')

    # Strip the column from the original CREATE TABLE
    col_pattern = re.compile(
        rf"\b{re.escape(column)}\b\s+[^,)\n]+,?\s*",
        re.IGNORECASE | re.MULTILINE,
    )
    new_sql = col_pattern.sub("", original_sql, count=1)
    # If we left a trailing comma, remove it
    new_sql = re.sub(r",\s*\)", ")", new_sql)
    # Build the new CREATE TABLE with the new table name, preserving quoting
    if quoted:
        new_table_ref = f'"{new_table}"'
    else:
        new_table_ref = new_table
    new_sql = re.sub(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?[\w]+\"?",
        f"CREATE TABLE {new_table_ref}",
        new_sql,
        count=1,
        flags=re.IGNORECASE,
    )

    conn.execute(f"DROP TABLE IF EXISTS {new_table}")
    conn.execute(new_sql)
    conn.execute(
        f"INSERT INTO {new_table} ({keep_cols_csv}) SELECT {keep_cols_csv} FROM {table}"
    )
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {new_table} RENAME TO {table}")

    # Recreate indexes that referenced the dropped column
    idx_rows = conn.execute(
        f"SELECT name, sql FROM sqlite_master "
        f"WHERE type='index' AND tbl_name='{table}' AND sql LIKE '%{column}%'"
    ).fetchall()
    for name, sql in idx_rows:
        try:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        except sqlite3.OperationalError:
            pass

    # Note: triggers and FKs that reference the dropped column will
    # be lost in the rebuild. For our case (parts_json, tool_call_id)
    # neither is referenced by any trigger, so we're safe.


def _migrate_text_part_ids(conn: sqlite3.Connection) -> None:
    """Assign deterministic ids to text parts missing one (text-part-routing).

    After the opencode-style 3-step text protocol (PR1), every text part
    in `parts_json` must carry an `id` field so the frontend can route
    streaming deltas to the correct segment via findLast by id.

    This migration scans all messages and assigns ``f"legacy-{msg_id}-{idx}"``
    to any text part without an id. Idempotent: parts that already have an
    id are left untouched. Failures are logged and skipped so a single
    corrupt row doesn't abort the whole migration.

    Level 2 / Phase 2 commit 6: parts_json column was dropped. This
    migration is a no-op (no rows to update) on post-migration DBs. We
    guard the SELECT with a column-existence check to avoid OperationalError.
    """
    if not _has_column(conn, "messages", "parts_json"):
        return  # post-migration DB; nothing to do
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


_db_thread_local = threading.local()


def _get_db() -> sqlite3.Connection:
    """Return the per-thread SQLite connection. Ensures schema is up to date.

    - One connection per worker thread (FastAPI serves requests on a
      thread pool): endpoints that used to open a fresh connection per
      request and rely on GC to close it now reuse a bounded set of
      connections (F1-3). Connections live for the thread's lifetime
      and are closed by GC at process exit; WAL mode keeps readers
      non-blocking.
    - The cache is keyed by db path (thread-local dict), so switching
      workspace paths (tests, multiple workspaces) opens a fresh
      connection instead of reusing a stale one.
    - WAL journal + busy_timeout: concurrent readers/writers no longer
      hit immediate ``database is locked`` (previously concurrent
      INSERT+UPDATE triggers could fail and messages were silently
      lost because persist_message swallows exceptions).
    - One-time schema migrations run inside _ensure_schema (guarded by
      PRAGMA user_version), not per connection.
    """
    db_path = str(_get_db_path())
    cache = getattr(_db_thread_local, "conns", None)
    if cache is None:
        cache = {}
        _db_thread_local.conns = cache
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _ensure_schema(conn)
        conn.commit()
        cache[db_path] = conn
    return conn


def _fetch_session_owned(conn: sqlite3.Connection, session_id: str, user_id: str) -> sqlite3.Row:
    """Fetch a session row, enforcing ownership (IDOR protection).

    Returns the row, or raises 404 (not found) / 403 (other user's).
    """
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


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
        # Fallback: legacy parts_json column (Level 2 pre-migration)
        parts = []
        if "parts_json" in row.keys() and row["parts_json"]:
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

        # Level 2 / Phase 2 commit 6: parts_json and tool_call_id
        # columns were dropped. Build the INSERT dynamically based on
        # which columns are present, so this function works on both
        # pre- and post-migration DBs during the cleanup window.
        has_parts_json = _has_column(conn, "messages", "parts_json")
        has_tool_call_id = _has_column(conn, "messages", "tool_call_id")

        cols = ["id", "session_id", "role", "content", "created_at", "metadata_json", "message_type", "seq"]
        placeholders = ["?", "?", "?", "?", "?", "?", "?", "?"]
        vals: list[Any] = [msg_id, session_id, role, content, ts, metadata_json, message_type, seq or 0]
        if has_parts_json:
            cols.append("parts_json")
            placeholders.append("?")
            vals.append(parts_json)
        if has_tool_call_id:
            cols.append("tool_call_id")
            placeholders.append("?")
            vals.append(tool_call_id)

        sql = (
            f"INSERT INTO messages ({','.join(cols)}) "
            f"VALUES ({','.join(placeholders)})"
        )
        conn.execute(sql, vals)
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
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
    return _row_to_session(row)


@router.patch("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session metadata: title, starred, archived, tags."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
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
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
    # messages FK CASCADE handles the delete; explicit delete also fine
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    _invalidate_projection(session_id)
    return {"status": "ok", "deleted_id": session_id}


def _invalidate_projection(session_id: str) -> None:
    """Drop the projector's in-memory state for a deleted session.

    The projector cache lives on the EventBusV2 held by the process-wide
    SessionService (owned by routers/chat.py). Lazy import avoids a
    web_session → chat module cycle.
    """
    try:
        from .chat import _session_service_cache
        for service in _session_service_cache.values():
            bus = getattr(service, "event_bus", None)
            invalidate = getattr(bus, "invalidate", None)
            if callable(invalidate):
                invalidate(session_id)
    except Exception as exc:
        logger.debug("projection invalidation skipped: %s", exc)


def delete_messages(session_id: str, message_ids: list[str]) -> None:
    """Delete specific messages by ID from a session."""
    if not message_ids:
        return
    try:
        conn = _get_db()
        placeholders = ",".join("?" for _ in message_ids)
        cur = conn.execute(
            f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
            [session_id, *message_ids],
        )
        # Only decrement by the number of rows actually deleted, so
        # message_count can never go negative when some ids don't exist.
        deleted = cur.rowcount
        conn.execute(
            "UPDATE sessions SET message_count = message_count - ?, updated_at = ? WHERE id = ?",
            (deleted, time.time(), session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("delete_messages failed for session %s: %s", session_id, exc)


def update_message_content(
    message_id: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Update a message's content (and optional parts).

    DELETE-CANDIDATE v0.6: FIXME broken; no callers; parts_json dropped by v2.
    FIXME(broken): no callers exist, and the ``parts is not None``
    branch writes to ``parts_json`` — a column dropped by the
    version-2 migration — so it would silently fail (caught + logged)
    and leave ``content`` un-updated too. Do not wire anything to this
    function until it is rewritten against the current
    messages/message_parts schema (or event-sourced projection).
    """
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
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
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
# Session state (B13): backfill agent / DAG / goal panels after reload.
# ─────────────────────────────────────────────────────────────────────────────


def _build_goal_snapshot(request: Request, session_id: str) -> dict | None:
    """Return the current goal snapshot dict for a session, or None.

    Pulls goal + criteria + evidence_count from GoalStore. The frontend
    Goal store expects a specific shape — the mapping in
    ``_shape_goal_for_frontend`` adapts the snapshot fields for that
    contract.
    """
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        with GoalStore(db_path=db_path) as store:
            return store.get_current_snapshot(session_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] goal snapshot failed: %s", e)
        return None


def _shape_goal_for_frontend(snapshot: dict | None) -> dict | None:
    """Map a GoalStore snapshot to the frontend Goal store shape.

    Frontend fields (see webui stores/goal.ts):
      goal_id, session_id, status, objective, progress_percent,
      criteria[{criterion_id, text, status, evidence_count}],
      evidence_count
    """
    if not snapshot:
        return None
    goal = snapshot.get("goal") or {}
    criteria = snapshot.get("criteria") or []
    return {
        "goal_id": goal.get("goal_id"),
        "session_id": goal.get("session_id", ""),
        "status": goal.get("status", "active"),
        "objective": goal.get("objective", ""),
        "progress_percent": _compute_goal_progress_percent(criteria),
        "criteria": [
            {
                "criterion_id": c.get("criterion_id", ""),
                "text": c.get("text", ""),
                "status": c.get("status", "pending"),
                "evidence_count": c.get("evidence_count", 0),
            }
            for c in criteria
        ],
        "evidence_count": snapshot.get("evidence_count", 0),
        "recap": goal.get("recap"),
    }


def _compute_goal_progress_percent(criteria: list[dict]) -> int:
    """0..100 — share of criteria with status 'covered' or 'complete'."""
    if not criteria:
        return 0
    done = sum(1 for c in criteria if c.get("status") in ("covered", "complete"))
    return int(round(done / len(criteria) * 100))


def _find_active_workflow_runner(session_id: str) -> tuple[str, Any] | None:
    """Find an active workflow runner (goal_id, runner) for a session.

    Walks ``routers.workflow._active_runners`` (in-memory). The runner's
    session_id is stored in the entry's ``session_id`` field — if it
    matches we return ``(goal_id, runner)``.
    """
    try:
        from .workflow import _active_runners, _prune_runners

        _prune_runners()
        for goal_id, entry in list(_active_runners.items()):
            if entry.get("session_id") == session_id:
                return goal_id, entry["runner"]
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] workflow runner lookup failed: %s", e)
    return None


def _shape_workflow_for_frontend(
    runner: Any,
    workflow_name: str,
) -> dict:
    """Map a workflow runner + its config to the frontend shape.

    Returns ``{name, nodes[], edges[], progress, agent_statuses}``.
    The frontend's workflow store expects ``nodes=[{id,label,status}]``
    and ``edges=[{id,source,target}]``; ``agent_statuses`` is the live
    runner dict used to derive per-node status.
    """
    config = getattr(runner, "_config", None)
    agent_status_map: dict[str, str] = {}
    progress: dict | None = None
    try:
        progress = runner.get_progress()
        agent_status_map = progress.get("agent_statuses", {}) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("[state] get_progress failed: %s", e)

    cfg_agents = getattr(config, "agents", []) if config else []
    cfg_dag = getattr(config, "dag", {}) if config else {}

    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", None) or ""
        # Map backend status → frontend DAG node status (pending |
        # running | completed | failed). Backend uses pending|running|
        # success|skipped|error; we collapse success+skipped →
        # completed and error → failed.
        raw = agent_status_map.get(agent_id, "pending")
        node_status = _MAP_AGENT_STATUS_TO_NODE.get(raw, "pending")
        nodes.append({
            "id": agent_id,
            "label": agent_id,
            "type": "agent",
            "status": node_status,
        })

    edges = []
    for src, targets in (cfg_dag or {}).items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})

    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": progress,
        "agent_statuses": agent_status_map,
    }


_MAP_AGENT_STATUS_TO_NODE = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
}


def _shape_agents_for_frontend(
    session_id: str,
    workflow: dict | None,
) -> list[dict]:
    """Build the per-session Agent store entries.

    Each entry matches the Agent interface in stores/agents.ts minimal
    fields: id, session_id, status, name, created_at, updated_at,
    tool_calls_count, compaction_count, context_tokens,
    context_tokens_limit, iterations_detail. We populate only what we
    can derive from the workflow snapshot — the SSE stream refreshes
    the rest after the backfill (e.g. token usage, iteration detail).
    """
    if not workflow:
        return []
    status_map = workflow.get("agent_statuses", {}) or {}
    nodes = workflow.get("nodes") or []
    now = time.time()
    agents = []
    for node in nodes:
        agent_id = node["id"]
        raw_status = status_map.get(agent_id, "pending")
        # Map backend status → frontend AgentStatus
        fe_status = _MAP_AGENT_STATUS_TO_AGENT.get(raw_status, "pending")
        agents.append({
            "id": agent_id,
            "session_id": session_id,
            "status": fe_status,
            "name": agent_id,
            "description": "",
            "created_at": now,
            "updated_at": now,
            "tool_calls_count": 0,
            "compaction_count": 0,
            "context_tokens": 0,
            "context_tokens_limit": 0,
            "iterations_detail": [],
        })
    return agents


_MAP_AGENT_STATUS_TO_AGENT = {
    "pending": "pending",
    "queued": "pending",
    "running": "running",
    "success": "completed",
    "skipped": "completed",
    "error": "failed",
    "completed": "completed",
    "failed": "failed",
    "aborted": "aborted",
}


@router.get("/{session_id}/state")
async def get_session_state(session_id: str, request: Request):
    """Backfill agent / DAG / goal panels after a page reload (B13).

    Aggregates three sources:
      1. GoalStore.get_current_snapshot — goal + criteria + evidence
      2. Active workflow runner (in-memory) — agent_statuses + DAG
      3. GoalWorkflowConfig — DAG structure (nodes/edges) for the
         session's active workflow

    Each subsection gracefully degrades to null / empty when the
    session has no active goal or no in-flight workflow (a finished
    workflow's runner is pruned by ``_prune_runners``; for those
    sessions we still surface the goal snapshot and the config-derived
    DAG via the goal's workflow_name, if available).

    Returns:
      {
        "goal": <frontend-shaped goal or null>,
        "workflow": <frontend-shaped workflow or null>,
        "agents": [<frontend-shaped agent>, ...]  # possibly empty
      }
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)

    # Goal snapshot
    snapshot = _build_goal_snapshot(request, session_id)
    goal = _shape_goal_for_frontend(snapshot)

    # Workflow: prefer a live runner, else attempt to derive the DAG
    # from the goal's stored workflow_name (preserved on goal record
    # by GoalWorkflowRunner.start).
    workflow: dict | None = None
    runner_match = _find_active_workflow_runner(session_id)
    if runner_match:
        _goal_id, runner = runner_match
        wf_name = getattr(getattr(runner, "_config", None), "name", "") or ""
        workflow = _shape_workflow_for_frontend(runner, wf_name)
    elif snapshot:
        # No live runner but the goal may have run a workflow — try to
        # surface the static DAG structure for the panel. GoalRecord
        # stores the workflow config name in the ``workflow_id`` column
        # (set by GoalWorkflowRunner.start — see workflow.py:378).
        goal_rec = snapshot.get("goal") or {}
        wf_name = goal_rec.get("workflow_id") or ""
        if wf_name:
            try:
                from ...core.goal.workflow_config import load_goal_workflow

                cfg = load_goal_workflow(wf_name)
                workflow = _shape_workflow_for_frontend_from_config(cfg, wf_name)
            except Exception as e:  # noqa: BLE001
                logger.debug("[state] static DAG rebuild failed: %s", e)

    agents = _shape_agents_for_frontend(session_id, workflow)

    return {
        "goal": goal,
        "workflow": workflow,
        "agents": agents,
    }


def _shape_workflow_for_frontend_from_config(cfg: Any, workflow_name: str) -> dict:
    """Build the workflow subsection from a config-only source.

    Used when there is no in-memory runner — the DAG and agent roster
    still come from the workflow's YAML. All agent statuses default to
    ``pending`` and ``progress`` is null (no live runner to ask).
    """
    cfg_agents = getattr(cfg, "agents", []) or []
    cfg_dag = getattr(cfg, "dag", {}) or {}
    nodes = []
    for agent_cfg in cfg_agents:
        agent_id = getattr(agent_cfg, "id", "") or ""
        nodes.append({"id": agent_id, "label": agent_id, "type": "agent", "status": "pending"})
    edges = []
    for src, targets in cfg_dag.items():
        for tgt in targets or []:
            edges.append({"id": f"{src}->{tgt}", "source": src, "target": tgt})
    return {
        "name": workflow_name,
        "nodes": nodes,
        "edges": edges,
        "progress": None,
        "agent_statuses": {},
    }


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
