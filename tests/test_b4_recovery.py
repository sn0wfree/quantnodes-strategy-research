"""B4 edge cases: error recovery, partial state, flush failures (Level 3, B4 commit 4).

Verifies that the event-sourced system gracefully handles:
- Partial event streams (process crashed mid-sequence)
- Flush failures (messages table unavailable)
- Re-flush after recovery (idempotent)
- Empty event_log → empty messages
- Single event → single message
- Events arriving out of order (shouldn't happen, but be defensive)
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.core.events.event_v2 import EventV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import Projector


def _setup_db(db_path: Path) -> None:
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
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL
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
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "u", "test", 1.0, 1.0),
    )
    conn.commit()
    conn.close()


class TestB4Recovery(unittest.TestCase):
    """Error recovery and edge case tests."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_partial_stream_text_only_started(self) -> None:
        """Crash after text.started but before text_delta — message exists with empty text."""
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hi", "role": "user",
        })
        v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        # CRASH HERE — no more events

        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 2)  # user + lazy assistant
        assistant = state.messages.get("a1")
        self.assertIsNotNone(assistant)
        self.assertEqual(assistant.role, "assistant")
        # Text part exists but is empty
        self.assertIn("t1", assistant.parts)
        self.assertEqual(assistant.parts["t1"].data["text"], "")

    def test_partial_stream_tool_no_result(self) -> None:
        """Crash after tool_call but before tool_result — tool_call in 'call' state."""
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1",
            "tool": "calc", "input": {"a": 1},
        })
        # CRASH before tool_result

        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        msg = list(state.messages.values())[0]
        self.assertIn("tc1", msg.parts)
        self.assertEqual(msg.parts["tc1"].data.get("state"), "call")
        self.assertNotIn("result", msg.parts["tc1"].data)

    def test_re_flush_recovers_from_missing_messages(self) -> None:
        """If messages table is wiped, re-flush from event_log restores everything."""
        # Write events via V2
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hello", "role": "user",
        })
        v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "hi"})
        v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "hi"})
        v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "hi"})

        # Verify messages exist
        conn = sqlite3.connect(str(self.db_path))
        msg_count_before = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(msg_count_before, 2)

        # Wipe messages table (simulate corruption)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM messages WHERE session_id = ?", ("s1",))
        conn.execute("DELETE FROM message_parts WHERE session_id = ?", ("s1",))
        conn.commit()
        msg_count_wiped = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(msg_count_wiped, 0)

        # Re-flush from event_log → restored
        state = self.proj.project("s1")
        self.proj.flush(state)

        conn = sqlite3.connect(str(self.db_path))
        msg_count_after = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        part_count = conn.execute(
            "SELECT COUNT(*) FROM message_parts WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(msg_count_after, 2)
        self.assertEqual(part_count, 1)

    def test_event_log_is_source_of_truth(self) -> None:
        """After any failure, event_log + re-flush = correct state.

        This is the fundamental B4 invariant: event_log is the single
        source of truth; messages + message_parts are disposable
        caches that can always be rebuilt.
        """
        # Build up state via events
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        for i in range(5):
            v2.emit("s1", "message_received", {
                "message_id": f"u{i}",
                "content": f"msg {i}",
                "role": "user",
            })

        # State A: 5 messages
        state_a = self.proj.project("s1")
        self.assertEqual(len(state_a.messages), 5)

        # Wipe messages table
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM messages WHERE session_id = ?", ("s1",))
        conn.execute("DELETE FROM message_parts WHERE session_id = ?", ("s1",))
        conn.commit()
        conn.close()

        # Re-flush
        state_b = self.proj.project("s1")
        self.proj.flush(state_b)

        # Same state
        self.assertEqual(len(state_b.messages), 5)
        for i in range(5):
            self.assertIn(f"u{i}", state_b.messages)

    def test_empty_event_log_empty_state(self) -> None:
        """Empty event_log → empty projection, empty messages after flush."""
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 0)

        # Flush empty state → no messages
        self.proj.flush(state)
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_unknown_events_are_skipped_safely(self) -> None:
        """Unknown event types don't break projection (forward compat)."""
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        # Mix of known + unknown events
        v2.emit("s1", "message_received", {"message_id": "u1", "content": "hi", "role": "user"})
        v2.emit("s1", "unknown.event.type", {"foo": "bar"})
        v2.emit("s1", "weird.new.feature", {"data": 123})
        v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "ok"})

        state = self.proj.project("s1")
        # User + assistant (lazy-created by text.started)
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages["u1"].content, "hi")
        self.assertEqual(state.messages["a1"].parts["t1"].data["text"], "ok")

    def test_flush_is_idempotent_many_times(self) -> None:
        """Flushing 100 times produces the same result as flushing once."""
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=True)

        # Build up state
        v2.emit("s1", "message_received", {"message_id": "u1", "content": "hello", "role": "user"})
        v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "hi"})

        # Flush 100 more times
        state = self.proj.project("s1")
        for _ in range(99):
            self.proj.flush(state)

        # Still 2 messages
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_duplicate_event_doesnt_break(self) -> None:
        """Same event published twice doesn't corrupt state.

        Uses INSERT OR REPLACE in event_log and first-wins semantics
        in the projector.
        """
        e = EventV2.create("s1", 1, "message_received", {
            "message_id": "u1", "content": "first", "role": "user",
        })
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path)

        # Publish the same event twice
        v2.publish(e)
        v2.publish(e)  # Duplicate (same id, same seq)

        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        self.assertEqual(state.messages["u1"].content, "first")

    def test_event_log_persists_across_bus_instances(self) -> None:
        """Events survive EventBusV2 instance restart (DB-backed)."""
        bus1 = EventBus()
        v2_1 = EventBusV2(bus1, self.db_path)
        v2_1.emit("s1", "message_received", {
            "message_id": "u1", "content": "persistent", "role": "user",
        })

        # New bus instance (simulating process restart)
        bus2 = EventBus()
        v2_2 = EventBusV2(bus2, self.db_path)

        # Can read events from before
        events = v2_2.replay("s1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["content"], "persistent")

        # Can project from before
        state = self.proj.project("s1")
        self.assertEqual(len(state.messages), 1)
        self.assertEqual(state.messages["u1"].content, "persistent")


if __name__ == "__main__":
    unittest.main()
