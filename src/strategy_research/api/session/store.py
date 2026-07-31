"""SessionStore: SQLite-backed persistence for Session, Message, Attempt.

Borrowed architecture from vibe_trading ``src/session/store.py`` (filesystem
JSONL + attempts dir), but adapted to reuse strategy-research's existing
SQLite ``web_session`` schema (which already has user_id, title, starred,
tags_json, etc.) and adds an ``attempts`` table.

This keeps all chat persistence in ONE DB (avoiding the dual-write problem
in vibe_trading) while gaining the ``Attempt`` model that tracks each
AgentLoop execution (metrics, react_trace, run_dir, error).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import Attempt, AttemptStatus, Message

logger = logging.getLogger(__name__)


class SessionStore:
    """SQLite-backed persistent storage for Session, Message, and Attempt.

    Reuses the existing webui DB schema (see
    ``api.routers.web_session._ensure_schema``). Session/messages go through
    ``persist_message`` and ``list_messages``; attempts go through dedicated
    CRUD below.

    Attributes:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Connection helper ──────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Message CRUD (delegates to existing persist_message) ───────────

    def append_message(
        self,
        message: Message,
        *,
        message_id: Optional[str] = None,
        parts: Optional[list[dict[str, Any]]] = None,
        created_at: Optional[float] = None,
        message_type: Optional[str] = None,
        seq: Optional[int] = None,
    ) -> str:
        """Append a message to the session.

        Args:
            message: Message to persist.
            message_id: Optional explicit ID (used by SSE event correlation
                for assistant messages). If None, a UUID is generated.
            parts: Optional structured parts (text/thinking/tool_call/etc.).
            created_at: Optional timestamp (epoch seconds). If None, uses
                time.time().
            message_type: One of 'user' | 'assistant' | 'tool' | 'compaction'.
                If None, uses message.role (user/assistant/tool).
            seq: Per-session monotonic sequence number (Level 1, opencode-aligned).
                If None, the column default (0) is used. Callers SHOULD pass
                an explicit seq from SeqGenerator for new messages.

        Returns:
            The message_id used.
        """
        # 默认使用 message.role，而不是固定 assistant
        if message_type is None:
            message_type = message.role

        logger.debug("[STORE] append_message session=%s role=%s type=%s content_len=%d seq=%s",
                    message.session_id, message.role, message_type, len(message.content), seq)

        # Lazy import to avoid circular dependency
        from ..routers.web_session import persist_message

        msg_id = message_id or str(uuid.uuid4())
        persist_message(
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            parts=parts or message.metadata.get("parts"),
            metadata=message.metadata,
            message_id=msg_id,
            created_at=created_at,
            tool_call_id=message.tool_call_id,
            message_type=message_type,
            seq=seq,
        )
        logger.debug("[STORE] persisted id=%s", msg_id)
        return msg_id

    def get_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[Message]:
        """Read all messages for a session (chronological order).

        Order key: `seq` (Level 1, opencode-aligned). `seq` is a
        per-session monotonic counter that eliminates clock-skew
        ambiguity. Falls back to `created_at` if seq is unavailable
        (defensive — should not happen after backfill).

        Level 2 / Phase 2 commit 5: parts are read from the
        message_parts table (via _row_to_message's `parts` param).
        Batch-fetched in a single query to avoid N+1.
        """
        logger.debug("[STORE] get_messages session=%s limit=%d", session_id, limit)
        from ..routers.web_session import _get_db, _row_to_message

        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY seq ASC, created_at ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()

            # Batch-fetch parts from message_parts (Level 2)
            parts_by_msg: dict[str, list[Any]] = {}
            if rows:
                message_ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(message_ids))
                parts_rows = conn.execute(
                    f"SELECT message_id, data_json FROM message_parts "
                    f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
                    message_ids,
                ).fetchall()
                for mid, data_json in parts_rows:
                    try:
                        part = json.loads(data_json)
                        if isinstance(part, dict):
                            parts_by_msg.setdefault(mid, []).append(part)
                    except (json.JSONDecodeError, TypeError):
                        pass

        out: list[Message] = []
        for r in rows:
            # Pass batch-fetched parts; missing key signals fallback
            # to parts_json for pre-migration rows
            m = _row_to_message(r, parts=parts_by_msg.get(r["id"]))
            # Defensive: _row_to_message returns dict, but keys may be missing
            # for post-migration DBs. Default sensibly.
            metadata = m.get("metadata") or {}
            out.append(
                Message(
                    message_id=m["id"],
                    session_id=session_id,
                    role=m["role"],
                    content=m.get("content", ""),
                    tool_call_id=m.get("tool_call_id"),
                    linked_attempt_id=metadata.get("linked_attempt_id"),
                    metadata={
                        **metadata,
                        "_parts": m.get("parts", []),
                    },
                    message_type=m.get("message_type", "assistant"),
                    seq=m.get("seq", 0),
                )
            )
        logger.debug("[STORE] loaded %d messages, types: %s", len(out),
                    dict(sorted({m.message_type: sum(1 for x in out if x.message_type == m.message_type)
                                for m in out}.items())))
        return out

    def get_session_metadata(self, session_id: str) -> Optional[dict[str, Any]]:
        """Return session metadata (title, message_count, starred, tags)."""
        from ..routers.web_session import get_session

        return get_session(session_id=session_id)

    # ── Attempt CRUD ───────────────────────────────────────────────────

    def create_attempt(self, attempt: Attempt) -> Attempt:
        """Persist a new Attempt in pending status."""
        created_at = attempt.created_at or _utc_now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO attempts (
                    attempt_id, session_id, parent_attempt_id, status,
                    prompt, run_dir, summary, react_trace_json, metrics_json,
                    created_at, completed_at, error, message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.session_id,
                    attempt.parent_attempt_id,
                    attempt.status.value,
                    attempt.prompt,
                    attempt.run_dir,
                    attempt.summary,
                    json.dumps(attempt.react_trace, ensure_ascii=False) if attempt.react_trace else None,
                    json.dumps(attempt.metrics, ensure_ascii=False) if attempt.metrics else None,
                    created_at,
                    attempt.completed_at,
                    attempt.error,
                    attempt.message_id,
                ),
            )
            conn.commit()
        attempt.created_at = created_at
        return attempt

    def update_attempt(self, attempt: Attempt) -> None:
        """Persist changes to an existing Attempt."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE attempts SET
                    status = ?,
                    prompt = ?,
                    run_dir = ?,
                    summary = ?,
                    react_trace_json = ?,
                    metrics_json = ?,
                    completed_at = ?,
                    error = ?,
                    message_id = ?
                WHERE attempt_id = ?
                """,
                (
                    attempt.status.value,
                    attempt.prompt,
                    attempt.run_dir,
                    attempt.summary,
                    json.dumps(attempt.react_trace, ensure_ascii=False) if attempt.react_trace else None,
                    json.dumps(attempt.metrics, ensure_ascii=False) if attempt.metrics else None,
                    attempt.completed_at,
                    attempt.error,
                    attempt.message_id,
                    attempt.attempt_id,
                ),
            )
            conn.commit()

    def get_attempt(self, session_id: str, attempt_id: str) -> Optional[Attempt]:
        """Read an Attempt by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE session_id = ? AND attempt_id = ?",
                (session_id, attempt_id),
            ).fetchone()
            if not row:
                return None
            return _row_to_attempt(row)

    def list_attempts(self, session_id: str, limit: int = 50) -> list[Attempt]:
        """List Attempts for a session, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM attempts WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [_row_to_attempt(r) for r in rows]


def _row_to_attempt(row: sqlite3.Row) -> Attempt:
    """Convert an attempts row to an Attempt dataclass."""
    return Attempt(
        attempt_id=row["attempt_id"],
        session_id=row["session_id"],
        parent_attempt_id=row["parent_attempt_id"],
        status=AttemptStatus(row["status"]),
        prompt=row["prompt"] or "",
        run_dir=row["run_dir"],
        summary=row["summary"],
        react_trace=json.loads(row["react_trace_json"]) if row["react_trace_json"] else [],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        error=row["error"],
        message_id=row["message_id"],
        metrics=json.loads(row["metrics_json"]) if row["metrics_json"] else None,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["SessionStore"]