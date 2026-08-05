"""Projector — derive message state from event_log (Level 3, B3 commit 1).

This module is the read-side complement to EventBusV2. It reads events
from event_log and projects them into the same shape as messages +
message_parts (the Level 2 PartTable). The projector is the
authoritative source of message state in the event-sourced architecture.

In B3, the projector becomes the primary read path: SessionStore reads
messages via projector instead of directly from the messages table.
The messages + message_parts tables become materialized views (cached
projection), and event_log is the source of truth."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .event_v2 import EventType, EventV2, is_known_event_type

logger = logging.getLogger(__name__)


# ── Projected state ────────────────────────────────────────────────


@dataclass
class ProjectedPart:
    """A part within a ProjectedMessage.

    Mirrors the message_parts.data_json shape (opencode Part model).
    """
    id: str
    type: str
    data: Dict[str, Any]
    seq: int = 0
    time_created: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "seq": self.seq,
            "time_created": self.time_created,
        }


@dataclass
class ProjectedMessage:
    """A message within a ProjectedSession.

    Mirrors the messages row shape (Level 2: content is the user
    text or assistant text summary; parts hold the rich content).
    """
    id: str
    session_id: str
    role: str
    content: str
    message_type: str = "assistant"
    created_at: float = 0.0
    seq: int = 0
    parts: Dict[str, ProjectedPart] = field(default_factory=dict)
    # attempt_id is for SSE correlation; not stored in messages table
    attempt_id: Optional[str] = None
    # Tracks the currently open thinking block so ``thinking_delta``
    # and ``thinking_end`` events (which arrive without their start's
    # seq) know which part to append to / close. Reset to ``None`` on
    # ``thinking_end``. Not persisted in the messages table — it's a
    # projector-in-memory bookkeeping field only.
    open_thinking_part_id: Optional[str] = None

    def parts_in_order(self) -> List[ProjectedPart]:
        """Return parts sorted by seq."""
        return sorted(self.parts.values(), key=lambda p: p.seq)


@dataclass
class ProjectedSession:
    """In-memory projection of a session's event_log."""
    session_id: str
    messages: Dict[str, ProjectedMessage] = field(default_factory=dict)
    last_seq: int = 0

    def messages_in_order(self) -> List[ProjectedMessage]:
        """Return messages sorted by seq."""
        return sorted(
            self.messages.values(),
            key=lambda m: (m.seq, m.created_at),
        )

    def to_message_rows(self) -> List[Dict[str, Any]]:
        """Serialize to the shape of messages table rows.

        Each row is a dict with the columns the messages table
        expects. parts are NOT included (they go in message_parts).
        """
        return [
            {
                "id": m.id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at,
                "seq": m.seq,
            }
            for m in self.messages_in_order()
        ]

    def to_part_rows(self) -> List[Dict[str, Any]]:
        """Serialize parts to the shape of message_parts rows."""
        rows: List[Dict[str, Any]] = []
        for m in self.messages_in_order():
            for p in m.parts_in_order():
                rows.append({
                    "id": p.id,
                    "message_id": m.id,
                    "session_id": m.session_id,
                    "type": p.type,
                    "data_json": json.dumps(p.data, ensure_ascii=False),
                    "seq": p.seq,
                    "time_created": p.time_created,
                })
        return rows

    def to_messages(self) -> List[Any]:
        """Convert to list[Message] (same shape as SessionStore.get_messages).

        Produces Message objects with parts stored in metadata["_parts"],
        matching the convention used by SessionStore.get_messages().

        created_at is converted from epoch float to ISO string to match
        the Message model convention.
        """
        from datetime import datetime, timezone

        from .models import Message

        out: List[Message] = []
        for m in self.messages_in_order():
            parts_list = [
                {
                    "id": p.id,
                    "type": p.type,
                    **p.data,
                }
                for p in m.parts_in_order()
            ]
            created_iso = datetime.fromtimestamp(
                m.created_at, tz=timezone.utc
            ).isoformat() if m.created_at > 0 else ""
            out.append(Message(
                message_id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                created_at=created_iso,
                message_type=m.message_type,
                seq=m.seq,
                metadata={
                    "_parts": parts_list,
                },
            ))
        return out


# ── Projector ──────────────────────────────────────────────────────


class Projector:
    """Read-side projector: events → ProjectedSession.

    The projector is a pure function (with respect to the DB):
    it reads event_log and produces an in-memory state. The B1
    test suite verifies that the projected state matches the
    live DB (messages + message_parts) for synthetic event streams.

    In B2, a write-side flush() method will atomically UPDATE
    messages + message_parts to match the projected state. For
    B1, the API is read-only.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        # Handler dispatch table: event_type → handler function
        self._handlers: Dict[str, Callable[[EventV2, ProjectedSession], None]] = {
            EventType.MESSAGE_RECEIVED: self._on_message_received,
            EventType.ASSISTANT_MESSAGE: self._on_assistant_message,
            EventType.TEXT_STARTED: self._on_text_started,
            EventType.TEXT_DELTA: self._on_text_delta,
            EventType.TEXT_ENDED: self._on_text_ended,
            EventType.TOOL_CALL: self._on_tool_call,
            EventType.TOOL_RESULT: self._on_tool_result,
            EventType.TOOL_PROGRESS: self._on_tool_progress,
            # Thinking blocks: each ``thinking_start`` opens a new
            # ProjectedPart (id = ``think_{event.seq}`` so concurrent
            # thinking blocks in the same message don't collide),
            # ``thinking_delta`` appends text, ``thinking_done`` /
            # ``thinking_end`` are no-ops (the part is sealed by
            # ``agent_done`` / DB reload; the frontend folds via
            # ``collapsed`` on the part itself).
            EventType.THINKING_START: self._on_thinking_start,
            EventType.THINKING_DELTA: self._on_thinking_delta,
            EventType.THINKING_DONE: self._on_thinking_done,
            EventType.THINKING_END: self._on_thinking_end,
            # Future-proof handlers for structured part events the
            # backend doesn't emit yet (the only consumer today is
            # tool_call). Wiring them up now means the next time a
            # contributor adds ``emit("file_edit", ...)`` or similar
            # to the AgentLoop, the projector already knows how to
            # persist the part — no silent B1-style ``lambda: None``
            # bug.
            EventType.FILE_EDIT: self._on_file_edit,
            EventType.TABLE: self._on_table,
            EventType.CHART: self._on_chart,
            EventType.IMAGE: self._on_image,
            # Compaction events create a compaction message (system)
            EventType.COMPACT: self._on_compact,
            EventType.COMPACT_ENDED: self._on_compact,
        }
        # Per-session in-memory projections, keyed by session_id. Only
        # used by project_incremental(); project() remains a pure
        # function. Entries are invalidated on session deletion.
        # See docs/projector-incremental.md.
        self._cache: Dict[str, ProjectedSession] = {}

    # ── Incremental projection (see docs/projector-incremental.md) ─

    def project_incremental(
        self,
        session_id: str,
        collect_touched: bool = False,
    ) -> Tuple[ProjectedSession, Optional[Set[str]]]:
        """Extend the cached projection with only the new events.

        On a cache hit, only events after the cached ``last_seq`` are
        replayed (O(delta) instead of O(N)). On a cache miss, falls
        back to a full ``project()``.

        Returns ``(state, touched)``:
        - ``state``: the (mutated) cached ProjectedSession.
        - ``touched``: set of message ids modified by the new events,
          or None when ``collect_touched`` is False. Contains the
          sentinel ``"*"`` when an event rewrites the whole session
          (compact.ended with a replacement message list) — callers
          must then fall back to a full flush.
        """
        touched: Optional[Set[str]] = set() if collect_touched else None
        cached = self._cache.get(session_id)
        if cached is not None:
            events = self.load_events(session_id, after_seq=cached.last_seq)
            if touched is not None:
                for event in events:
                    self._collect_touched(event, touched)
            for event in events:
                self._apply(event, cached)
            if events:
                cached.last_seq = max(e.seq for e in events)
            return cached, touched

        # Cache miss — full rebuild. touched=None so the caller falls
        # back to a full flush (all rows written, stale rows deleted).
        state = self.project(session_id)
        self._cache[session_id] = state
        return state, None

    def invalidate(self, session_id: str) -> None:
        """Drop the cached projection for a session (session deleted).

        The next project_incremental() for this session does a full
        rebuild — correct because event_log is the source of truth.
        """
        self._cache.pop(session_id, None)

    def _collect_touched(self, event: EventV2, touched: Set[str]) -> None:
        """Record which message ids an event modifies (for delta flush).

        Every handler resolves its target message via
        ``data["message_id"]`` (or ``user_message_id`` for
        message_received), so those keys enumerate the touched set.
        Compact events with a full replacement list rewrite every
        message — signalled with the ``"*"`` sentinel.
        """
        data = event.data
        for key in ("message_id", "user_message_id"):
            mid = data.get(key)
            if mid:
                touched.add(mid)
        if event.type in (EventType.COMPACT, EventType.COMPACT_ENDED):
            if isinstance(data.get("messages"), list):
                touched.add("*")
            else:
                # Simple marker mode (L4 auto-compaction without
                # replacement list): explicitly record the marker id
                # so the delta flush actually writes it to the
                # messages table.  Without this, the marker is
                # created in the in-memory state but never persisted
                # because ``touched`` stays empty.
                marker_id = f"compact-{event.id[:8]}"
                touched.add(marker_id)

    @staticmethod
    def _message_row(msg: ProjectedMessage) -> Dict[str, Any]:
        """Serialize a single message to a messages-table row."""
        return {
            "id": msg.id,
            "session_id": msg.session_id,
            "role": msg.role,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at,
            "seq": msg.seq,
        }

    @staticmethod
    def _part_rows(msg: ProjectedMessage) -> List[Dict[str, Any]]:
        """Serialize a single message's parts to message_parts rows."""
        return [
            {
                "id": p.id,
                "message_id": msg.id,
                "session_id": msg.session_id,
                "type": p.type,
                "data_json": json.dumps(p.data, ensure_ascii=False),
                "seq": p.seq,
                "time_created": p.time_created,
            }
            for p in msg.parts_in_order()
        ]

    # ── Public API ──────────────────────────────────────────────

    def load_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> List[EventV2]:
        """Read events from event_log for a session."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, aggregate_id, seq, type, data_json, time_created "
                "FROM event_log WHERE aggregate_id = ? AND seq > ? "
                "ORDER BY seq ASC",
                (session_id, after_seq),
            ).fetchall()
        finally:
            conn.close()
        return [EventV2.from_row(r) for r in rows]

    def project(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> ProjectedSession:
        """Build a ProjectedSession from event_log.

        Returns a fresh ProjectedSession each call. Multiple calls
        with the same args produce identical state (pure function).
        """
        state = ProjectedSession(session_id=session_id)
        events = self.load_events(session_id, after_seq=after_seq)
        for event in events:
            self._apply(event, state)
        state.last_seq = max((e.seq for e in events), default=0)
        return state

    def apply(self, event: EventV2, state: ProjectedSession) -> None:
        """Apply a single event to an existing ProjectedSession.

        Used by:
        - project() above to build state from a full event stream
        - Tests to verify individual event handling
        - Future: live projector that maintains state in memory
        """
        self._apply(event, state)

    # ── Flush (B2) ──────────────────────────────────────────────

    def flush(
        self,
        state: ProjectedSession,
        touched: Optional[Set[str]] = None,
    ) -> None:
        """Atomically UPSERT the projected state to messages + message_parts.

        Idempotent: calling flush() twice with the same state produces
        the same result (no duplicate rows). Uses INSERT OR REPLACE
        for both messages and message_parts.

        Safety:
        - Runs in a single transaction
        - Only touches rows belonging to state.session_id
        - Deletes message_parts rows that no longer exist in the
          projection (handles part deletion edge cases)

        Args:
            touched: Optional set of message ids to write (delta
                flush, see docs/projector-incremental.md). Only those
                messages are UPSERTed and their parts rewritten.
                ``None`` or a set containing the ``"*"`` sentinel
                (whole-session rewrite, e.g. compact.ended) performs a
                full flush.

        This is the B2 write path. In B2, service.py publishes events
        via EventBusV2 which writes to event_log, then calls flush()
        to materialize the state to messages + message_parts for the
        existing read path (SessionStore.get_messages).

        Eventually (B3+), the read path will use the projector
        directly and messages + message_parts can be removed.
        """
        full = touched is None or "*" in touched
        msg_rows = state.to_message_rows() if full else None
        part_rows = state.to_part_rows() if full else None

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN")

            if full:
                # ── Full flush: UPSERT everything ──────────────
                # Note: message.id is the table's PRIMARY KEY.
                # Production message ids are UUIDs (unique per call),
                # so INSERT OR REPLACE works correctly for the
                # standard case. For tests with colliding ids across
                # sessions, the previous code had a cross-session
                # overwrite bug; tests should use unique ids per
                # session to avoid this.
                #
                # metadata_json: the projection doesn't carry
                # metadata, so a plain REPLACE would NULL out
                # model/run_id/etc. Use ON CONFLICT DO UPDATE that
                # preserves the existing metadata when the incoming
                # row has none.
                msg_ids = {row["id"] for row in msg_rows}
                for row in msg_rows:
                    self._upsert_message(conn, row)

                # B5: Delete messages that no longer exist in the
                # projection (e.g., after compact.ended removed them).
                # Without this, old messages linger in the DB and the
                # invariant breaks.
                if msg_ids:
                    placeholders = ",".join("?" * len(msg_ids))
                    conn.execute(
                        f"DELETE FROM messages "
                        f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                        (state.session_id, *msg_ids),
                    )
                else:
                    conn.execute(
                        "DELETE FROM messages WHERE session_id = ?",
                        (state.session_id,),
                    )

                # UPSERT message_parts
                for row in part_rows:
                    self._insert_part(conn, row)

                # Delete parts that no longer exist (session scope)
                part_ids = {r["id"] for r in part_rows}
                if part_ids:
                    placeholders = ",".join("?" * len(part_ids))
                    conn.execute(
                        f"DELETE FROM message_parts "
                        f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                        (state.session_id, *part_ids),
                    )
                else:
                    conn.execute(
                        "DELETE FROM message_parts WHERE session_id = ?",
                        (state.session_id,),
                    )
            else:
                # ── Delta flush: only touched messages ──────────
                # Each touched message is UPSERTed and its parts
                # rewritten (DELETE + INSERT, idempotent). Messages
                # are never deleted in the delta path — only compact
                # events remove messages, and those signal "*".
                for mid in sorted(touched):
                    msg = state.messages.get(mid)
                    if msg is None:
                        continue
                    self._upsert_message(conn, self._message_row(msg))
                    conn.execute(
                        "DELETE FROM message_parts WHERE message_id = ?",
                        (mid,),
                    )
                    for row in self._part_rows(msg):
                        self._insert_part(conn, row)

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @staticmethod
    def _upsert_message(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """UPSERT a single messages-table row (preserves metadata_json)."""
        conn.execute(
            "INSERT INTO messages "
            "(id, session_id, role, content, created_at, "
            "message_type, seq, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET "
            "session_id=excluded.session_id, "
            "role=excluded.role, "
            "content=excluded.content, "
            "created_at=excluded.created_at, "
            "message_type=excluded.message_type, "
            "seq=excluded.seq, "
            "metadata_json=COALESCE(excluded.metadata_json, "
            "                      messages.metadata_json)",
            (
                row["id"],
                row["session_id"],
                row["role"],
                row["content"],
                row["created_at"],
                row["message_type"],
                row["seq"],
            ),
        )

    @staticmethod
    def _insert_part(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """INSERT OR REPLACE a single message_parts row."""
        conn.execute(
            "INSERT OR REPLACE INTO message_parts "
            "(id, message_id, session_id, type, data_json, "
            "seq, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["message_id"],
                row["session_id"],
                row["type"],
                row["data_json"],
                row["seq"],
                row["time_created"],
            ),
        )

    def project_and_flush(self, session_id: str) -> ProjectedSession:
        """Convenience: project + flush in one call."""
        state = self.project(session_id)
        self.flush(state)
        return state

    def project_to_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[Any]:
        """Project event_log → list[Message] (B3 primary read path).

        Drop-in replacement for SessionStore.get_messages(). Returns
        the same Message objects with parts in metadata["_parts"].

        Args:
            session_id: Session to project.
            limit: Max messages to return (first N by seq, matching
                SessionStore.get_messages behavior).

        Returns:
            List of Message in chronological order (by seq).
        """
        state = self.project(session_id)
        messages = state.to_messages()
        if limit and len(messages) > limit:
            # First N by seq, matching SessionStore.get_messages() /
            # the DB read path (ORDER BY seq ASC LIMIT N).
            messages = messages[:limit]
        return messages

    # ── Event handlers ──────────────────────────────────────────

    def _apply(self, event: EventV2, state: ProjectedSession) -> None:
        if event.aggregate_id != state.session_id:
            logger.warning(
                "Projector: event aggregate_id %s != state session_id %s",
                event.aggregate_id, state.session_id,
            )
            return
        handler = self._handlers.get(event.type)
        if handler is None:
            if not is_known_event_type(event.type):
                logger.debug(
                    "Projector: skipping unknown event type %r", event.type
                )
            else:
                # Known type but no handler — not yet implemented
                logger.debug(
                    "Projector: no handler for known type %r (B1 scope)",
                    event.type,
                )
            return
        try:
            handler(event, state)
        except Exception as exc:
            logger.exception(
                "Projector: handler for %s raised: %s", event.type, exc,
            )

    def _on_message_received(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Handle user message: INSERT messages row (role=user)."""
        data = event.data
        message_id = data.get("message_id") or data.get("user_message_id")
        if not message_id:
            logger.warning(
                "Projector: message_received without message_id (seq=%s)",
                event.seq,
            )
            return
        if message_id in state.messages:
            # Duplicate — first event wins
            return
        msg_seq = len(state.messages) + 1
        state.messages[message_id] = ProjectedMessage(
            id=message_id,
            session_id=event.aggregate_id,
            role="user",
            content=data.get("content", ""),
            message_type="user",
            created_at=event.time_created,
            seq=msg_seq,
        )

    def _on_assistant_message(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Handle assistant message boundary.

        The assistant_message event is emitted at the END of an
        iteration (loop.py:582) carrying the final accumulated
        content. We treat it as the message-row boundary.

        But: text/tool events are emitted BEFORE this event, so
        the message_id is needed earlier. The event_callback in
        service.py adds attempt.message_id to every event's
        data, so we use that for the message_id here.
        """
        message_id = event.data.get("message_id")
        if not message_id:
            logger.warning(
                "Projector: assistant_message without message_id (seq=%s)",
                event.seq,
            )
            return
        msg_type = event.data.get("message_type", "assistant")
        if message_id in state.messages:
            # Update content and type with the final summary
            state.messages[message_id].content = event.data.get("content", "")
            state.messages[message_id].message_type = msg_type
            return
        msg_seq = len(state.messages) + 1
        state.messages[message_id] = ProjectedMessage(
            id=message_id,
            session_id=event.aggregate_id,
            role="assistant",
            content=event.data.get("content", ""),
            message_type=msg_type,
            created_at=event.time_created,
            seq=msg_seq,
            attempt_id=event.data.get("attempt_id"),
        )

    def _ensure_assistant_message(
        self, event: EventV2, state: ProjectedSession,
    ) -> Optional[ProjectedMessage]:
        """Lazy-create an assistant message on first text/tool event.

        The assistant_message event may not have arrived yet (it
        comes at the end of the iteration), but text.started /
        tool.call events need a parent message_id. If the message
        doesn't exist, create it with empty content and let the
        final assistant_message event update the content.
        """
        message_id = event.data.get("message_id")
        if not message_id:
            return None
        if message_id in state.messages:
            return state.messages[message_id]
        # Lazy-create
        msg_seq = len(state.messages) + 1
        msg = ProjectedMessage(
            id=message_id,
            session_id=event.aggregate_id,
            role="assistant",
            content="",
            message_type="assistant",
            created_at=event.time_created,
            seq=msg_seq,
            attempt_id=event.data.get("attempt_id"),
        )
        state.messages[message_id] = msg
        return msg

    def _on_text_started(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        text_id = event.data.get("text_id") or str(uuid.uuid4())
        if text_id in msg.parts:
            return
        msg.parts[text_id] = ProjectedPart(
            id=text_id,
            type="text",
            data={"type": "text", "id": text_id, "text": ""},
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_text_delta(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        text_id = event.data.get("text_id")
        if not text_id or text_id not in msg.parts:
            # text.started might have been missed; create lazily
            text_id = text_id or str(uuid.uuid4())
            msg.parts[text_id] = ProjectedPart(
                id=text_id,
                type="text",
                data={"type": "text", "id": text_id, "text": ""},
                seq=len(msg.parts),
                time_created=event.time_created,
            )
        part = msg.parts[text_id]
        part.data["text"] = part.data.get("text", "") + event.data.get("text", "")

    def _on_text_ended(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        text_id = event.data.get("text_id")
        if not text_id or text_id not in msg.parts:
            return
        part = msg.parts[text_id]
        # text.ended carries the final text; prefer it if present
        if "text" in event.data:
            part.data["text"] = event.data["text"]

    def _on_tool_call(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        tc_id = event.data.get("id") or event.data.get("call_id")
        if not tc_id:
            logger.warning(
                "Projector: tool_call without id (seq=%s)", event.seq,
            )
            return
        if tc_id in msg.parts:
            return
        # Build tool_call data in the frontend ToolCallPart shape
        # (name/arguments/status) AND keep backend-compatible fields
        # (tool/input/state) so _convert_messages_to_history still works.
        #
        # Two input event shapes are supported:
        # 1. Flat: {tool, name, id, arguments} — loop._emit("tool_call") shape
        # 2. Nested: {function: {name, arguments}} — LLM API style
        #
        # Frontend ToolCallPart reads: name, arguments, status, result, progress.
        # If these are missing on DB reload, ToolCallBlock shows blank icons
        # and undefined status → "tool call 标识消失". So we MUST populate them.
        data: Dict[str, Any] = {
            "type": "tool_call",
            "id": tc_id,
            "state": "call",
            "status": "running",
        }
        # Tool name resolution: prefer flat `name` (loop.py emits both
        # `tool` and `name`), fall back to `tool`, then function.name.
        function = event.data.get("function")
        if isinstance(function, dict):
            data["function"] = function
            data["tool"] = function.get("name", "")
            data["name"] = event.data.get("name") or data["tool"]
        else:
            data["tool"] = event.data.get("tool", "")
            data["name"] = event.data.get("name") or data["tool"]
        # Arguments resolution: prefer flat `arguments` (loop.py shape),
        # fall back to `input`, then function.arguments.
        if "arguments" in event.data:
            data["arguments"] = event.data["arguments"]
            data["input"] = event.data["arguments"]
        elif "input" in event.data:
            data["arguments"] = event.data["input"]
            data["input"] = event.data["input"]
        elif isinstance(function, dict) and "arguments" in function:
            data["arguments"] = function["arguments"]
            data["input"] = function["arguments"]

        msg.parts[tc_id] = ProjectedPart(
            id=tc_id,
            type="tool_call",
            data=data,
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_tool_result(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Update the tool_call part with the result.

        This is the opencode pattern: tool_result is a STATE
        UPDATE to an existing tool_call part, not a new part.
        The part's data.status becomes "done" and data.result
        is filled in.
        """
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        tc_id = event.data.get("id") or event.data.get("call_id")
        if not tc_id:
            logger.warning(
                "Projector: tool_result without id (seq=%s)", event.seq,
            )
            return
        part = msg.parts.get(tc_id)
        if part is None:
            # Tool result arrived before tool_call (shouldn't happen,
            # but be defensive). Create the part now.
            part = ProjectedPart(
                id=tc_id,
                type="tool_call",
                data={"type": "tool_call", "id": tc_id},
                seq=len(msg.parts),
                time_created=event.time_created,
            )
            msg.parts[tc_id] = part
        part.data["result"] = event.data.get("result", event.data.get("preview", ""))
        part.data["status"] = event.data.get("status", "done")
        part.data["state"] = "done"
        # Ensure name/arguments survive (defensive for tool_result-first cases)
        if "name" not in part.data:
            part.data["name"] = event.data.get("name") or event.data.get("tool", "")
        if "arguments" not in part.data:
            part.data["arguments"] = event.data.get("arguments") or event.data.get("input", "")

    def _on_tool_progress(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Tool progress is metadata on the tool_call part."""
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        tc_id = event.data.get("id") or event.data.get("call_id")
        if not tc_id or tc_id not in msg.parts:
            return
        part = msg.parts[tc_id]
        progress = part.data.get("progress", [])
        progress.append({
            "stage": event.data.get("stage", ""),
            "current": event.data.get("current"),
            "total": event.data.get("total"),
            "message": event.data.get("message", ""),
            "time": event.time_created,
        })
        part.data["progress"] = progress

    # ── Thinking blocks (B7-fix: was lambda: None) ──────────────

    def _on_thinking_start(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Open a new thinking block.

        The backend's ``thinking_start`` event has no ``thinking_id``
        field (unlike ``text_id``) — each block is scoped to the
        current message. We derive a stable part_id from
        ``event.seq`` so concurrent thinking blocks in the same
        message don't collide (verified: a single message can have
        3+ thinking blocks when the LLM alternates between thinking
        and tool calls). The part_id is recorded in
        ``ProjectedMessage.open_thinking_part_id`` so subsequent
        ``thinking_delta`` and ``thinking_end`` events know which
        part to append to / close.
        """
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = f"think_{event.seq}"
        if part_id in msg.parts:
            # Idempotent on replay: re-emission of the same start
            # event must not create a duplicate part. The existing
            # part's open state is restored so subsequent deltas
            # still append to the right block.
            msg.open_thinking_part_id = part_id
            return
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="thinking",
            data={"type": "thinking", "text": "", "collapsed": True},
            seq=len(msg.parts),
            time_created=event.time_created,
        )
        msg.open_thinking_part_id = part_id

    def _on_thinking_delta(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Append the delta to the currently-open thinking block.

        If no thinking block is open (the matching ``thinking_start``
        was missed — e.g. event loss during reconnect / replay), we
        lazy-create one keyed by ``event.seq`` so the text is not
        lost. Mirrors the same fallback in ``_on_text_delta``.
        """
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = msg.open_thinking_part_id
        if part_id is None or part_id not in msg.parts:
            # Lazy create — fall back to the seq-keyed id. This
            # creates a separate part from the one the missing start
            # would have opened. We set the new part as the open one
            # so subsequent deltas and the eventual end land in the
            # same part.
            part_id = f"think_{event.seq}"
            msg.parts[part_id] = ProjectedPart(
                id=part_id,
                type="thinking",
                data={"type": "thinking", "text": "", "collapsed": True},
                seq=len(msg.parts),
                time_created=event.time_created,
            )
            msg.open_thinking_part_id = part_id
        part = msg.parts[part_id]
        part.data["text"] = part.data.get("text", "") + event.data.get("delta", "")

    def _on_thinking_done(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """No-op: ``thinking_done`` is informational, the part is
        already accumulated by ``thinking_delta``. We leave collapse
        state as-is so the frontend can decide based on its own
        streaming flags. The part remains "open" (in case more
        deltas arrive) until ``thinking_end`` is seen.
        """
        return

    def _on_thinking_end(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Close the currently-open thinking block.

        The thinking part is now sealed — subsequent ``thinking_delta``
        events (which shouldn't happen, but might on a replay
        boundary) will lazy-create a new part. The persisted part
        keeps ``collapsed: True`` so DB reloads render folded.
        """
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        # Only clear the open pointer if it's still set; a second
        # ``thinking_end`` (replay) is then a true no-op.
        if msg.open_thinking_part_id is not None:
            msg.open_thinking_part_id = None

    # ── Future-proof part handlers (B7-2: defense-in-depth) ──────
    #
    # The backend's AgentLoop does NOT currently emit file_edit /
    # table / chart / image events (see docs/todo or
    # webui/frontend/src/hooks/sse/types.ts:43-49 for the full
    # design intent). Wiring them here is *insurance* — if a future
    # contributor adds ``emit("file_edit", ...)`` to loop.py, the
    # projector will already know how to persist the part instead of
    # silently dropping it like the original thinking bug.

    def _on_file_edit(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = event.data.get("id") or f"file_edit_{event.seq}"
        if part_id in msg.parts:
            return
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="file_edit",
            data={
                "type": "file_edit",
                "file_path": event.data.get("file_path", ""),
                "old_content": event.data.get("old_content", ""),
                "new_content": event.data.get("new_content", ""),
            },
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_table(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = event.data.get("id") or f"table_{event.seq}"
        if part_id in msg.parts:
            return
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="table",
            data={
                "type": "table",
                "headers": event.data.get("headers", []),
                "rows": event.data.get("rows", []),
                "caption": event.data.get("caption"),
            },
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_chart(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = event.data.get("id") or f"chart_{event.seq}"
        if part_id in msg.parts:
            return
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="chart",
            data={
                "type": "chart",
                "chart_type": event.data.get("chart_type", "bar"),
                "data": event.data.get("data", []),
                "title": event.data.get("title"),
            },
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_image(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        msg = self._ensure_assistant_message(event, state)
        if msg is None:
            return
        part_id = event.data.get("id") or f"image_{event.seq}"
        if part_id in msg.parts:
            return
        msg.parts[part_id] = ProjectedPart(
            id=part_id,
            type="image",
            data={
                "type": "image",
                "url": event.data.get("url", ""),
                "alt": event.data.get("alt"),
            },
            seq=len(msg.parts),
            time_created=event.time_created,
        )

    def _on_compact(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Handle compaction event — create a compaction message.

        Compaction messages are system-level messages that mark where
        the history was compressed. They have role='system' and
        message_type='compaction'. The content is the summary.

        If the event has a 'messages' field (compact.ended with full
        replacement data), we replace the pre-compaction messages with
        the compressed set, keeping only the most recent message
        (the current turn) plus the compaction message itself.
        """
        data = event.data
        summary = data.get("summary") or data.get("content") or ""
        msg_id = f"compact-{event.id[:8]}"

        # If messages list is provided, do full replacement
        compressed_msgs = data.get("messages")
        if compressed_msgs and isinstance(compressed_msgs, list):
            # Save the last message (current turn, not compressed)
            ordered = state.messages_in_order()
            last_msg = ordered[-1] if ordered else None

            # Clear all existing messages
            state.messages.clear()

            # Insert compressed messages
            for i, m_data in enumerate(compressed_msgs):
                role = m_data.get("role", "assistant")
                content = m_data.get("content", "") or ""
                cid = m_data.get("id") or f"cmp_{event.id[:8]}_{i}"

                pmsg = ProjectedMessage(
                    id=cid,
                    session_id=event.aggregate_id,
                    role=role,
                    content=content,
                    message_type=role if role in ("user", "assistant", "system") else "assistant",
                    created_at=event.time_created,
                    seq=len(state.messages) + 1,
                )

                # Add tool_call parts if present
                tool_calls = m_data.get("tool_calls")
                if role == "assistant" and tool_calls:
                    for j, tc in enumerate(tool_calls):
                        tc_id = tc.get("id", "") or f"tc_{i}_{j}"
                        func = tc.get("function", {})
                        pmsg.parts[tc_id] = ProjectedPart(
                            id=tc_id,
                            type="tool_call",
                            data={
                                "type": "tool_call",
                                "id": tc_id,
                                "state": "done",
                                "tool": func.get("name", ""),
                                "input": func.get("arguments", "{}"),
                                "result": tc.get("result", ""),
                                "status": "done",
                            },
                            seq=len(pmsg.parts),
                            time_created=event.time_created,
                        )

                state.messages[cid] = pmsg

            # Insert the compaction marker message
            state.messages[msg_id] = ProjectedMessage(
                id=msg_id,
                session_id=event.aggregate_id,
                role="system",
                content=summary,
                message_type="compaction",
                created_at=event.time_created,
                seq=len(state.messages) + 1,
            )

            # Re-add the last message (current turn) if it existed
            if last_msg:
                last_msg.seq = len(state.messages) + 1
                state.messages[last_msg.id] = last_msg

            return

        # Simple case: just add a compaction marker message
        if msg_id in state.messages:
            if summary:
                state.messages[msg_id].content = summary
            return
        msg_seq = len(state.messages) + 1
        state.messages[msg_id] = ProjectedMessage(
            id=msg_id,
            session_id=event.aggregate_id,
            role="system",
            content=summary,
            message_type="compaction",
            created_at=event.time_created,
            seq=msg_seq,
        )


__all__ = [
    "Projector",
    "ProjectedSession",
    "ProjectedMessage",
    "ProjectedPart",
]
