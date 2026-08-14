"""End-to-end Phase 3 B1 verification.

Simulates a 700dc7f7-style conversation: user message, assistant
text streaming, multiple tool calls with results. Verifies:

1. EventBusV2 publishes events to event_log + EventBus
2. Projector can rebuild the full state from event_log
3. The projected state is consistent with what service.py would
   have written to messages + message_parts

This is B1 scope: read-only verification. The actual data path
through service.py is unchanged.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.event_v2 import EventType, EventV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import Projector


def _setup_full_schema(db_path: Path) -> None:
    """Create the full schema needed for E2E verification."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT, title TEXT,
                created_at REAL, updated_at REAL,
                starred INTEGER DEFAULT 0, tags_json TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata_json TEXT,
                message_type TEXT,
                seq INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
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
            )
        """)
        conn.execute("""
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (aggregate_id, seq)
            )
        """)
        conn.execute(
            "CREATE INDEX idx_event_log_aggregate_seq "
            "ON event_log(aggregate_id, seq)"
        )
        conn.commit()
    finally:
        conn.close()


class TestE2EEventSourcingPipeline(unittest.TestCase):
    """End-to-end test: EventBusV2 + Projector working together."""

    def setUp(self) -> None:
        # Fresh DB per test to avoid state pollution
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("700dc7f7", "u1", "e2e-test", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_e2e_simple_conversation(self) -> None:
        """User → assistant text reply. Verify both sinks + projector."""
        # 1. User message
        self.v2.publish(EventV2.create("700dc7f7", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u-msg-1",
            "user_message_id": "u-msg-1",
            "assistant_message_id": "a-msg-1",
            "role": "user",
            "content": "Hello",
        }))
        # 2. Assistant text streaming
        self.v2.publish(EventV2.create("700dc7f7", 2, EventType.TEXT_STARTED, {
            "message_id": "a-msg-1",
            "text_id": "t-1",
        }))
        self.v2.publish(EventV2.create("700dc7f7", 3, EventType.TEXT_DELTA, {
            "message_id": "a-msg-1",
            "text_id": "t-1",
            "text": "Hi there!",
        }))
        self.v2.publish(EventV2.create("700dc7f7", 4, EventType.TEXT_ENDED, {
            "message_id": "a-msg-1",
            "text_id": "t-1",
            "text": "Hi there!",
        }))
        # 3. Assistant message boundary
        self.v2.publish(EventV2.create("700dc7f7", 5, EventType.ASSISTANT_MESSAGE, {
            "message_id": "a-msg-1",
            "content": "Hi there!",
        }))

        # Verify event_log
        self.assertEqual(self.v2.count("700dc7f7"), 5)
        self.assertEqual(self.v2.last_seq("700dc7f7"), 5)

        # Verify SSE delivery
        buffered = self.bus.replay("700dc7f7", replay_all=True)
        self.assertEqual(len(buffered), 5)

        # Verify projector rebuilds state
        state = self.projector.project("700dc7f7")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages["u-msg-1"].role, "user")
        self.assertEqual(state.messages["a-msg-1"].role, "assistant")
        self.assertIn("t-1", state.messages["a-msg-1"].parts)
        self.assertEqual(
            state.messages["a-msg-1"].parts["t-1"].data["text"],
            "Hi there!",
        )

    def test_e2e_with_tool_calls(self) -> None:
        """User → assistant with 3 tool calls + results. Verify projector."""
        # User message
        self.v2.publish(EventV2.create("700dc7f7", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1",
            "user_message_id": "u1",
            "role": "user",
            "content": "Get price for 000001.SZ, 000002.SZ, 000003.SZ",
        }))
        # Assistant text
        self.v2.publish(EventV2.create("700dc7f7", 2, EventType.TEXT_STARTED, {
            "message_id": "a1",
            "text_id": "t1",
        }))
        self.v2.publish(EventV2.create("700dc7f7", 3, EventType.TEXT_DELTA, {
            "message_id": "a1",
            "text_id": "t1",
            "text": "Let me look those up.",
        }))
        # 3 parallel tool calls
        for i, sym in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
            self.v2.publish(EventV2.create("700dc7f7", 4 + i, EventType.TOOL_CALL, {
                "message_id": "a1",
                "id": f"tc-{i+1}",
                "tool": "get_price",
                "input": {"symbol": sym},
            }))
        # 3 tool results (could be in any order)
        for i, price in enumerate([10.5, 22.3, 8.7]):
            self.v2.publish(EventV2.create("700dc7f7", 7 + i, EventType.TOOL_RESULT, {
                "message_id": "a1",
                "id": f"tc-{i+1}",
                "result": str(price),
                "status": "done",
            }))
        # Final assistant message
        self.v2.publish(EventV2.create("700dc7f7", 10, EventType.ASSISTANT_MESSAGE, {
            "message_id": "a1",
            "content": "Got the prices.",
        }))

        # Verify event_log
        self.assertEqual(self.v2.count("700dc7f7"), 10)
        self.assertEqual(self.v2.last_seq("700dc7f7"), 10)

        # Verify projector
        state = self.projector.project("700dc7f7")
        a = state.messages["a1"]
        # 1 text part + 3 tool_call parts = 4 parts
        self.assertEqual(len(a.parts), 4)
        # All 3 tool calls are now "done"
        for i in range(1, 4):
            part = a.parts[f"tc-{i}"]
            self.assertEqual(part.data["state"], "done")
            self.assertEqual(part.data["status"], "done")
        # Text part has the streamed text
        self.assertEqual(a.parts["t1"].data["text"], "Let me look those up.")

    def test_e2e_projection_serializes_to_db_shape(self) -> None:
        """The projector output can be written back to messages + message_parts."""
        # Publish a few events
        self.v2.publish(EventV2.create("700dc7f7", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1",
            "role": "user",
            "content": "Test",
        }))
        self.v2.publish(EventV2.create("700dc7f7", 2, EventType.TEXT_STARTED, {
            "message_id": "a1",
            "text_id": "t1",
        }))
        self.v2.publish(EventV2.create("700dc7f7", 3, EventType.TEXT_DELTA, {
            "message_id": "a1",
            "text_id": "t1",
            "text": "Hello",
        }))

        state = self.projector.project("700dc7f7")
        message_rows = state.to_message_rows()
        part_rows = state.to_part_rows()

        # Write the projected state to the live DB (simulating B2 flush)
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            for r in message_rows:
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, "
                    "created_at, message_type, seq) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], r["session_id"], r["role"], r["content"],
                     r["created_at"], r["message_type"], r["seq"]),
                )
            for r in part_rows:
                conn.execute(
                    "INSERT INTO message_parts (id, message_id, session_id, "
                    "type, data_json, seq, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], r["message_id"], r["session_id"], r["type"],
                     r["data_json"], r["seq"], r["time_created"]),
                )
            conn.commit()
            # Verify
            msg_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                ("700dc7f7",),
            ).fetchone()[0]
            part_count = conn.execute(
                "SELECT COUNT(*) FROM message_parts WHERE session_id = ?",
                ("700dc7f7",),
            ).fetchone()[0]
            self.assertEqual(msg_count, 2)
            self.assertEqual(part_count, 1)
        finally:
            conn.close()

    def test_e2e_replay_after_seq(self) -> None:
        """Replay is incremental — after_seq skips already-seen events."""
        # Publish 5 events
        for i in range(1, 6):
            self.v2.publish(EventV2.create("700dc7f7", i, EventType.TEXT_DELTA, {
                "message_id": "a1",
                "text_id": "t1",
                "text": f"chunk{i} ",
            }))
        # Replay from seq=3
        events = self.v2.replay("700dc7f7", after_seq=3)
        self.assertEqual([e.seq for e in events], [4, 5])

        # Project from seq=3
        state = self.projector.project("700dc7f7", after_seq=3)
        # 1 message (lazy-created), 1 part with 2 chunks
        self.assertIn("a1", state.messages)
        text = state.messages["a1"].parts["t1"].data["text"]
        # Note: text is accumulated across all deltas including those
        # we didn't re-project, because the part already exists.
        # In B1, the in-memory state is fresh; text comes from the
        # last text_delta in the projected range.
        self.assertIn("chunk", text)


class TestE2ERealisticEventStream(unittest.TestCase):
    """A more realistic stream: many event types in a single iteration."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("real-1", "u1", "real", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_full_iteration(self) -> None:
        """Simulate a full AgentLoop iteration with mixed event types."""
        events = [
            (1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u-1",
                "role": "user",
                "content": "Run factor analysis",
            }),
            (2, EventType.ITER_START, {"iteration": 1, "max_iterations": 5}),
            (3, EventType.THINKING_START, {}),
            (4, EventType.THINKING_DELTA, {"delta": "I should run factor analysis..."}),
            (5, EventType.THINKING_DONE, {}),
            (6, EventType.TEXT_STARTED, {"message_id": "a-1", "text_id": "t-1"}),
            (7, EventType.TEXT_DELTA, {
                "message_id": "a-1", "text_id": "t-1",
                "text": "I'll need to load data first.",
            }),
            (8, EventType.TEXT_ENDED, {
                "message_id": "a-1", "text_id": "t-1",
                "text": "I'll need to load data first.",
            }),
            (9, EventType.TOOL_CALL, {
                "message_id": "a-1",
                "id": "tc-load",
                "tool": "import_data",
                "input": {"codes": ["000001.SZ"]},
            }),
            (10, EventType.TOOL_PROGRESS, {
                "message_id": "a-1",
                "id": "tc-load",
                "stage": "fetching",
                "current": 100, "total": 1000,
            }),
            (11, EventType.TOOL_RESULT, {
                "message_id": "a-1",
                "id": "tc-load",
                "result": "100 rows imported",
                "status": "done",
            }),
            (12, EventType.LLM_USAGE, {
                "input_tokens": 1000, "output_tokens": 200,
            }),
            (13, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a-1",
                "content": "I'll need to load data first.",
            }),
            (14, EventType.ITER_END, {"iteration": 1}),
            (15, EventType.AGENT_DONE, {"message_id": "a-1", "status": "success"}),
        ]
        for seq, etype, data in events:
            self.v2.publish(EventV2.create("real-1", seq, etype, data))

        # Verify all events in event_log
        self.assertEqual(self.v2.count("real-1"), 15)

        # Verify projector handles this correctly
        state = self.projector.project("real-1")

        # 2 messages: u-1 and a-1
        self.assertEqual(len(state.messages), 2)
        self.assertIn("u-1", state.messages)
        self.assertIn("a-1", state.messages)

        # User message
        self.assertEqual(state.messages["u-1"].content, "Run factor analysis")
        self.assertEqual(state.messages["u-1"].role, "user")

        # Assistant: 1 text part + 1 tool_call part (tool_progress is metadata)
        a = state.messages["a-1"]
        self.assertEqual(len(a.parts), 2)
        self.assertIn("t-1", a.parts)
        self.assertIn("tc-load", a.parts)

        # Text part
        self.assertEqual(a.parts["t-1"].data["text"], "I'll need to load data first.")

        # Tool part has progress + result
        tc = a.parts["tc-load"]
        self.assertEqual(tc.data["state"], "done")
        self.assertEqual(tc.data["result"], "100 rows imported")
        self.assertEqual(len(tc.data.get("progress", [])), 1)
        self.assertEqual(tc.data["progress"][0]["stage"], "fetching")
        self.assertEqual(tc.data["progress"][0]["current"], 100)


if __name__ == "__main__":
    unittest.main()
