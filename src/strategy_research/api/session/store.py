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
from typing import Any, Optional, Sequence

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
        # B3: read from event_log via projector? Controlled by env var
        # so we can toggle without code changes.
        # Default: enabled (event_log is the source of truth in B3).
        # Set SR_EVENT_LOG_READ=0 to fall back to direct DB reads.
        import os
        self._use_event_log_read = os.environ.get("SR_EVENT_LOG_READ", "1") != "0"

    # ── Connection helper ──────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Message CRUD (delegates to existing persist_message) ───────────

    # TODO(architecture): append_message / get_session_metadata /
    # list_attempts are legacy direct-write paths with no production
    # callers. The B4 event-sourcing change replaced direct writes with
    # EventBusV2 emission + projector flush (and messages are read via
    # the projector). get_session_metadata would crash if called — it
    # imports the ASYNC router endpoint get_session and calls it
    # DELETE-CANDIDATE v0.6: 0 production callers; keep list_attempts_by_status.
    # synchronously. Remove when the transition window (docs/
    # compaction-summary-fix.md B4) closes.

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

        Level 3 / B3: by default, reads from event_log via the projector
        (event_log is the source of truth; messages + message_parts
        are materialized views). Set SR_EVENT_LOG_READ=0 to fall
        back to direct DB reads.
        """
        if self._use_event_log_read:
            return self._get_messages_from_event_log(session_id, limit)
        return self._get_messages_from_db(session_id, limit)

    def _get_messages_from_db(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[Message]:
        """Read messages from messages + message_parts tables (legacy path)."""
        logger.debug("[STORE] get_messages (db) session=%s limit=%d", session_id, limit)
        from ..routers.web_session import _get_db, _row_to_message

        with _get_db() as conn:
            conn.row_factory = sqlite3.Row
            # Most-recent N (chronological): DESC + reverse. Previously
            # this took the OLDEST N, breaking LLM context in long chats.
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY seq DESC, created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            rows = list(reversed(rows))

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
            m = _row_to_message(r, parts=parts_by_msg.get(r["id"]))
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
        logger.debug("[STORE] loaded %d messages (db), types: %s", len(out),
                    dict(sorted({m.message_type: sum(1 for x in out if x.message_type == m.message_type)
                                for m in out}.items())))
        return out

    def _get_messages_from_event_log(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[Message]:
        """Read messages from event_log via projector (B3 path).

        event_log is the source of truth; the projector rebuilds
        message state from the event stream. This is the B3 read
        path that replaces direct messages table reads.

        Falls back to DB path if event_log is empty or projector
        fails (defensive).
        """
        logger.debug("[STORE] get_messages (event_log) session=%s limit=%d", session_id, limit)
        try:
            from .projector import Projector
            proj = Projector(self.db_path)
            msgs = proj.project_to_messages(session_id, limit=limit)
            logger.debug("[STORE] loaded %d messages (event_log)", len(msgs))
            return msgs
        except Exception as exc:
            logger.warning(
                "[STORE] event_log read failed for %s: %s — falling back to DB",
                session_id, exc,
            )
            return self._get_messages_from_db(session_id, limit)

    # DELETE-CANDIDATE v0.6: 0 production callers.
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
                    created_at, completed_at, error, message_id, persona,
                    mode, model_override, thinking
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    attempt.persona,
                    attempt.mode,
                    attempt.model_override,
                    attempt.thinking,
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
                    message_id = ?,
                    mode = ?,
                    model_override = ?,
                    thinking = ?
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
                    attempt.mode,
                    attempt.model_override,
                    attempt.thinking,
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

    # DELETE-CANDIDATE v0.6: 0 production callers; use list_attempts_by_status.
    def list_attempts(self, session_id: str, limit: int = 50) -> list[Attempt]:
        """List Attempts for a session, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM attempts WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [_row_to_attempt(r) for r in rows]

    def list_attempts_by_status(
        self,
        session_id: str,
        statuses: Sequence[str],
    ) -> list[Attempt]:
        """List Attempts in the given statuses, oldest first.

        Used by the reload-recovery path: the endpoint rebuilds the
        frontend streaming/queued state from the attempts table, with
        in-memory guards applied by the caller (see
        docs/streaming-reload-recovery.md).
        """
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM attempts WHERE session_id = ? "
                f"AND status IN ({placeholders}) "
                "ORDER BY created_at ASC",
                (session_id, *statuses),
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
        persona=row["persona"] if "persona" in row.keys() else None,
        mode=row["mode"] if "mode" in row.keys() else "build",
        model_override=row["model_override"] if "model_override" in row.keys() else None,
        thinking=row["thinking"] if "thinking" in row.keys() else "auto",
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["SessionStore"]
