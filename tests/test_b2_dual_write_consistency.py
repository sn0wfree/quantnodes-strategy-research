"""B2 dual-write consistency tests (Level 3, B2 commit 3).

Verifies that events published via EventBusV2 (the same path service.py
uses in B2) can be projected back into the same shape as what the
direct write path (messages + message_parts) produces.

This is the key B2 invariant: event_log projection == direct writes.
If this holds, we can safely switch the read path to the projector
in B3/B4.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import Projector


def _setup_db(db_path: Path) -> None:
    """Create all tables (sessions, messages, message_parts, event_log)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            message_type TEXT NOT NULL DEFAULT 'assistant',
            seq INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        CREATE INDEX idx_event_log_aggregate_seq ON event_log(aggregate_id, seq);
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "u", "test", 1.0, 1.0),
    )
    conn.commit()
    conn.close()


class TestB2DualWriteConsistency(unittest.TestCase):
    """Verify event_log projection == direct messages + message_parts writes.

    In B2, service.py does BOTH:
    1. Writes directly to messages + message_parts (existing path)
    2. Emits events via EventBusV2 → event_log (new path)

    This test simulates both paths and verifies they produce the same
    result when projected.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _write_user_message_direct(self, msg_id: str, content: str) -> None:
        """Simulate service.py direct write of a user message."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "created_at, message_type, seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, "s1", "user", content, 1.0, "user", 1),
        )
        conn.commit()
        conn.close()

    def _write_assistant_message_direct(
        self, msg_id: str, content: str, parts: list | None = None,
    ) -> None:
        """Simulate service.py direct write of an assistant message + parts."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "created_at, message_type, seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, "s1", "assistant", content, 2.0, "assistant", 2),
        )
        if parts:
            for i, part in enumerate(parts):
                conn.execute(
                    "INSERT INTO message_parts (id, message_id, session_id, "
                    "type, data_json, seq, time_created) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?)",
                    (
                        part["id"], msg_id, "s1", part["type"],
                        json.dumps(part, ensure_ascii=False),
                        i, 2.0,
                    ),
                )
        conn.commit()
        conn.close()

    def test_user_message_consistency(self) -> None:
        """User message: direct write == projected from event_log."""
        # Path 1: direct write
        self._write_user_message_direct("u1", "hello world")

        # Path 2: event_log (same data)
        self.v2.emit("s1", "message_received", {
            "message_id": "u1",
            "user_message_id": "u1",
            "content": "hello world",
            "role": "user",
        })

        # Project
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        msg = list(state.messages.values())[0]
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "hello world")

        # Compare with direct write
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT id, role, content, message_type FROM messages WHERE id = ?",
            ("u1",),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], msg.id)
        self.assertEqual(row[1], msg.role)
        self.assertEqual(row[2], msg.content)

    def test_assistant_text_consistency(self) -> None:
        """Assistant text: direct write + parts == projected from event_log."""
        # Path 1: direct write
        self._write_assistant_message_direct(
            "a1", "Hi there",
            parts=[{
                "type": "text",
                "id": "t1",
                "text": "Hi there",
            }],
        )

        # Path 2: event_log (3-step text protocol)
        self.v2.emit("s1", "text.started", {
            "message_id": "a1",
            "text_id": "t1",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1",
            "text_id": "t1",
            "text": "Hi ",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1",
            "text_id": "t1",
            "text": "there",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1",
            "text_id": "t1",
            "text": "Hi there",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1",
            "content": "Hi there",
        })

        # Project
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        msg = list(state.messages.values())[0]
        self.assertEqual(msg.role, "assistant")
        self.assertEqual(msg.content, "Hi there")
        self.assertEqual(len(msg.parts), 1)
        self.assertIn("t1", msg.parts)
        self.assertEqual(msg.parts["t1"].data["text"], "Hi there")

        # Compare with direct write
        conn = sqlite3.connect(str(self.db_path))
        direct_msg = conn.execute(
            "SELECT id, role, content FROM messages WHERE id = ?",
            ("a1",),
        ).fetchone()
        direct_part = conn.execute(
            "SELECT id, type, data_json FROM message_parts WHERE message_id = ?",
            ("a1",),
        ).fetchone()
        conn.close()

        self.assertEqual(direct_msg[1], msg.role)
        self.assertEqual(direct_msg[2], msg.content)

        part_data = json.loads(direct_part[2])
        self.assertEqual(part_data["text"], msg.parts["t1"].data["text"])
        self.assertEqual(part_data["type"], msg.parts["t1"].data["type"])

    def test_tool_call_consistency(self) -> None:
        """Tool call + result: direct write == projected."""
        # Path 1: direct write
        self._write_assistant_message_direct(
            "a1", "result is 3",
            parts=[{
                "type": "tool_call",
                "id": "tc1",
                "tool": "calc",
                "input": {"a": 1, "b": 2},
                "result": "3",
                "status": "done",
                "state": "done",
            }],
        )

        # Path 2: event_log
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1",
            "id": "tc1",
            "tool": "calc",
            "input": {"a": 1, "b": 2},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1",
            "id": "tc1",
            "result": "3",
            "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1",
            "content": "result is 3",
        })

        # Project
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        msg = list(state.messages.values())[0]
        self.assertEqual(len(msg.parts), 1)
        part = msg.parts["tc1"]
        self.assertEqual(part.type, "tool_call")
        self.assertEqual(part.data["tool"], "calc")
        self.assertEqual(part.data["result"], "3")
        self.assertEqual(part.data["state"], "done")

    def test_full_conversation_consistency(self) -> None:
        """Full conversation: user + assistant + tool == direct writes."""
        # User message
        self._write_user_message_direct("u1", "calculate 1+2")
        self.v2.emit("s1", "message_received", {
            "message_id": "u1",
            "user_message_id": "u1",
            "content": "calculate 1+2",
            "role": "user",
        })

        # Assistant with text + tool call + tool result
        self._write_assistant_message_direct(
            "a1", "The result is 3",
            parts=[
                {
                    "type": "text",
                    "id": "t1",
                    "text": "Let me calculate that...",
                },
                {
                    "type": "tool_call",
                    "id": "tc1",
                    "tool": "calc",
                    "input": {"expr": "1+2"},
                    "result": "3",
                    "status": "done",
                    "state": "done",
                },
            ],
        )

        self.v2.emit("s1", "text.started", {
            "message_id": "a1", "text_id": "t1",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1",
            "text": "Let me calculate that...",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1",
            "text": "Let me calculate that...",
        })
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1",
            "tool": "calc", "input": {"expr": "1+2"},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1",
            "result": "3", "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1",
            "content": "The result is 3",
        })

        # Project and compare count
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 2)

        # Verify message order
        ordered = state.messages_in_order()
        self.assertEqual(ordered[0].role, "user")
        self.assertEqual(ordered[0].content, "calculate 1+2")
        self.assertEqual(ordered[1].role, "assistant")
        self.assertEqual(ordered[1].content, "The result is 3")

        # Verify part count
        self.assertEqual(len(ordered[1].parts), 2)

    def test_flush_matches_direct_write(self) -> None:
        """Projector.flush() produces the same rows as direct writes.

        This is the critical B2 invariant: if we project from event_log
        and flush to messages + message_parts, the resulting rows should
        be equivalent to what service.py writes directly.
        """
        # Path 1: direct writes
        self._write_user_message_direct("u1", "hello")
        self._write_assistant_message_direct(
            "a1", "hi",
            parts=[{
                "type": "text", "id": "t1", "text": "hi",
            }],
        )

        # Path 2: event_log
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hello",
            "role": "user", "user_message_id": "u1",
        })
        self.v2.emit("s1", "text.started", {
            "message_id": "a1", "text_id": "t1",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "hi",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1", "text": "hi",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "hi",
        })

        # Project from event_log
        state = self.proj.project("s1")

        # Delete direct-write rows to see what flush produces
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM message_parts WHERE session_id = ?", ("s1",))
        conn.execute("DELETE FROM messages WHERE session_id = ?", ("s1",))
        conn.commit()
        conn.close()

        # Flush projected state
        self.proj.flush(state)

        # Verify messages are the same
        conn = sqlite3.connect(str(self.db_path))
        msgs = conn.execute(
            "SELECT id, role, content, message_type FROM messages "
            "WHERE session_id = ? ORDER BY seq",
            ("s1",),
        ).fetchall()
        parts = conn.execute(
            "SELECT id, message_id, type, data_json FROM message_parts "
            "WHERE session_id = ? ORDER BY seq",
            ("s1",),
        ).fetchall()
        conn.close()

        # Should have 2 messages (user + assistant)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0][1], "user")
        self.assertEqual(msgs[0][2], "hello")
        self.assertEqual(msgs[1][1], "assistant")
        self.assertEqual(msgs[1][2], "hi")

        # Should have 1 part (the text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][2], "text")
        part_data = json.loads(parts[0][3])
        self.assertEqual(part_data["text"], "hi")

    def test_event_count_matches_emits(self) -> None:
        """Number of events in event_log matches number of emit calls."""
        # Emit 10 events
        for i in range(10):
            self.v2.emit("s1", "text_delta", {
                "message_id": "a1",
                "text_id": "t1",
                "text": f"chunk {i}",
            })

        self.assertEqual(self.v2.count("s1"), 10)

    def test_emit_returns_sse_event(self) -> None:
        """emit() returns SSEEvent (same as EventBus.emit)."""
        result = self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hi",
        })

        # Check it's an SSEEvent with the right data
        self.assertEqual(result.event_type, "message_received")
        self.assertEqual(result.data["content"], "hi")
        self.assertEqual(result.session_id, "s1")
        self.assertIsNotNone(result.event_id)

    def test_legacy_bus_receives_events(self) -> None:
        """Events emitted via V2 also reach the legacy EventBus.

        B2 backward compat: existing SSE subscribers still receive
        all events because EventBusV2 forwards to the legacy bus.
        """
        # Emit 3 events via V2
        self.v2.emit("s1", "message_received", {"message_id": "u1"})
        self.v2.emit("s1", "text_delta", {"text": "hi"})
        self.v2.emit("s1", "agent_done", {"status": "success"})

        # They should be in the legacy bus's buffer
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 3)
        self.assertEqual(buffered[0].event_type, "message_received")
        self.assertEqual(buffered[1].event_type, "text_delta")
        self.assertEqual(buffered[2].event_type, "agent_done")
        # Event IDs match between V2 and legacy bus
        self.assertEqual(buffered[0].data["message_id"], "u1")
        self.assertEqual(buffered[1].data["text"], "hi")
        self.assertEqual(buffered[2].data["status"], "success")


if __name__ == "__main__":
    unittest.main()
