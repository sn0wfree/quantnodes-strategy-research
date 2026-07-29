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
    ) -> str:
        """Append a message to the session.

        Args:
            message: Message to persist.
            message_id: Optional explicit ID (used by SSE event correlation
                for assistant messages). If None, a UUID is generated.
            parts: Optional structured parts (text/thinking/tool_call/etc.).

        Returns:
            The message_id used.
        """
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
        )
        return msg_id

    def get_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[Message]:
        """Read all messages for a session (chronological order)."""
        from ..routers.web_session import _get_db, _row_to_message

        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        out: list[Message] = []
        for r in rows:
            m = _row_to_message(r)
            out.append(
                Message(
                    message_id=m["id"],
                    session_id=session_id,
                    role=m["role"],
                    content=m.get("content", ""),
                    linked_attempt_id=m.get("metadata", {}).get("linked_attempt_id"),
                    metadata=m.get("metadata", {}),
                )
            )
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