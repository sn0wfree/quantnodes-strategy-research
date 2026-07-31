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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
        from .models import Message
        from datetime import datetime, timezone

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
            # Note: thinking events are absorbed but not stored as parts
            # in B1; they're preserved in event_log for future use.
            EventType.THINKING_START: lambda e, s: None,
            EventType.THINKING_DELTA: lambda e, s: None,
            EventType.THINKING_DONE: lambda e, s: None,
            EventType.THINKING_END: lambda e, s: None,
            # Compaction events create a compaction message (system)
            EventType.COMPACT: self._on_compact,
            EventType.COMPACT_ENDED: self._on_compact,
        }

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

    def flush(self, state: ProjectedSession) -> None:
        """Atomically UPSERT the projected state to messages + message_parts.

        Idempotent: calling flush() twice with the same state produces
        the same result (no duplicate rows). Uses INSERT OR REPLACE
        for both messages and message_parts.

        Safety:
        - Runs in a single transaction
        - Only touches rows belonging to state.session_id
        - Deletes message_parts rows that no longer exist in the
          projection (handles part deletion edge cases)

        This is the B2 write path. In B2, service.py publishes events
        via EventBusV2 which writes to event_log, then calls flush()
        to materialize the state to messages + message_parts for the
        existing read path (SessionStore.get_messages).

        Eventually (B3+), the read path will use the projector
        directly and messages + message_parts can be removed.
        """
        msg_rows = state.to_message_rows()
        part_rows = state.to_part_rows()
        part_ids = {r["id"] for r in part_rows}

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN")

            # UPSERT messages
            for row in msg_rows:
                conn.execute(
                    "INSERT OR REPLACE INTO messages "
                    "(id, session_id, role, content, created_at, "
                    "message_type, seq, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
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

            # UPSERT message_parts
            for row in part_rows:
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

            # Delete parts that no longer exist (for this session only)
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

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

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
        if message_id in state.messages:
            # Update content with the final summary
            state.messages[message_id].content = event.data.get("content", "")
            return
        msg_seq = len(state.messages) + 1
        state.messages[message_id] = ProjectedMessage(
            id=message_id,
            session_id=event.aggregate_id,
            role="assistant",
            content=event.data.get("content", ""),
            message_type="assistant",
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
        # Build tool_call data: opencode Part shape + preserve any
        # LLM-API-style "function" sub-object if present.
        #
        # Two input shapes are supported:
        # 1. Flat: {tool, input} — used by service.py after event_callback
        #    flattens
        # 2. Nested: {function: {name, arguments}} — LLM API style
        #    (arguments may be a JSON string, not a dict)
        #
        # Both shapes are preserved: the part's `tool` and `input`
        # fields provide the opencode Part view, while `function`
        # is preserved verbatim if it was in the event data.
        data: Dict[str, Any] = {
            "type": "tool_call",
            "id": tc_id,
            "state": "call",
        }
        # Tool name resolution: prefer flat `tool`, fall back to function.name
        function = event.data.get("function")
        if isinstance(function, dict):
            data["function"] = function
            data["tool"] = function.get("name", "")
        else:
            data["tool"] = event.data.get("tool", "")
        # Input/arguments resolution: prefer flat `input`, fall back to arguments
        if "input" in event.data:
            data["input"] = event.data["input"]
        elif "arguments" in event.data:
            data["input"] = event.data["arguments"]
        elif isinstance(function, dict) and "arguments" in function:
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

    def _on_compact(
        self, event: EventV2, state: ProjectedSession,
    ) -> None:
        """Handle compaction event — create a compaction message.

        Compaction messages are system-level messages that mark where
        the history was compressed. They have role='system' and
        message_type='compaction'. The content is the summary.
        """
        data = event.data
        summary = data.get("summary") or data.get("content") or ""
        # Generate a deterministic ID from the event id
        msg_id = f"compact-{event.id[:8]}"
        if msg_id in state.messages:
            # Update content if we have a newer version
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
