"""B2 backward compatibility: SSE bridge, SSE buffer, compact handler (Level 3, B2 commit 5).

Verifies that after switching to EventBusV2, ALL existing systems
that depended on EventBus still work:
1. bridge.py (EventBus → SSEEventBuffer) — still receives events
2. sse_buffer.push — still called for every event
3. EventBus.set_loop — still works (async delivery)
4. Compact command handler — uses service.event_bus.emit()
5. EventBus.replay — still works on legacy bus
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus, SSEEvent
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


class TestB2BackwardCompat(unittest.TestCase):
    """B2 backward compatibility: all existing systems still work."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)

        # IMPORTANT: We must attach the bridge before creating V2,
        # because V2 forwards to self.event_bus.publish(), which is what
        # the bridge monkey-patches. Since _bridge_attached is a
        # module-level guard, we create a FRESH EventBus for each test
        # and manually attach the bridge by copying the pattern.
        self.legacy_bus = EventBus()
        # Attach bridge manually (bypass the singleton guard)
        self._attach_bridge_to_bus(self.legacy_bus)
        self.v2 = EventBusV2(self.legacy_bus, self.db_path)
        self.proj = Projector(self.db_path)

        # Clear sse_buffer for test isolation
        from strategy_research.api.sse_buffer import sse_buffer
        sse_buffer._buffer.clear()
        sse_buffer._counter = 0

    @staticmethod
    def _attach_bridge_to_bus(bus: EventBus) -> None:
        """Attach SSE bridge to a specific bus instance.

        Mirrors what attach_eventbus_to_sse does, but bypasses the
        module-level singleton guard for test isolation.
        """
        from strategy_research.api.sse_buffer import sse_buffer

        original_publish = bus.publish

        def bridged_publish(event: SSEEvent) -> None:
            original_publish(event)
            try:
                sse_buffer.push(
                    event.event_type,
                    json.dumps(event.data, ensure_ascii=False),
                    event.session_id,
                )
            except Exception:
                pass

        bus.publish = bridged_publish

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_sse_buffer_receives_events_via_v2(self) -> None:
        """SSE buffer receives events when emitted via EventBusV2.

        The bridge (attach_eventbus_to_sse) monkey-patches the legacy
        EventBus.publish. Since EventBusV2 forwards to the legacy bus,
        events should still reach the SSE buffer.
        """
        from strategy_research.api.sse_buffer import sse_buffer

        # Push a known sentinel first so we can detect new events
        sentinel_id = sse_buffer.push("test_sentinel", "{}", "s1")

        # Emit via V2
        self.v2.emit("s1", "message_received", {"message_id": "u1"})
        self.v2.emit("s1", "text_delta", {"text": "hi"})
        self.v2.emit("s1", "agent_done", {"status": "ok"})

        # Get events since sentinel
        events = sse_buffer.get_events_since("s1", sentinel_id)
        self.assertGreaterEqual(len(events), 3)

        event_types = [e.event for e in events]
        self.assertIn("message_received", event_types)
        self.assertIn("text_delta", event_types)
        self.assertIn("agent_done", event_types)

    def test_bridge_data_roundtrip(self) -> None:
        """Event data is faithfully passed through V2 → bridge → sse_buffer."""
        from strategy_research.api.sse_buffer import sse_buffer

        test_data = {
            "message_id": "msg-123",
            "content": "你好世界",
            "status": "streaming",
            "nested": {"a": 1, "b": [1, 2, 3]},
        }
        self.v2.emit("s1", "message_received", test_data)

        events = sse_buffer.get_events_since("s1", "")
        # Find our event
        msg_events = [e for e in events if e.event == "message_received"]
        self.assertGreaterEqual(len(msg_events), 1)

        data = json.loads(msg_events[-1].data)
        self.assertEqual(data["message_id"], "msg-123")
        self.assertEqual(data["content"], "你好世界")
        self.assertEqual(data["nested"]["a"], 1)

    def test_legacy_bus_replay_still_works(self) -> None:
        """EventBus.replay() (legacy) still works with V2 in front."""
        # Emit via V2
        for i in range(5):
            self.v2.emit("s1", "text_delta", {"index": i})

        # Replay via legacy bus
        events = self.legacy_bus.replay("s1", replay_all=True)
        self.assertGreaterEqual(len(events), 5)

        # Verify they're SSEEvent objects with correct types
        for e in events:
            self.assertIsInstance(e, SSEEvent)
            self.assertIsNotNone(e.event_id)
            self.assertEqual(e.session_id, "s1")

    def test_event_bus_set_loop_still_works(self) -> None:
        """EventBus.set_loop() still works (async subscriber delivery).

        The set_loop is called on the legacy bus in app.py, and since
        V2 just forwards, async delivery to subscribers still works.
        """
        import asyncio

        received = []
        loop = asyncio.new_event_loop()
        self.legacy_bus.set_loop(loop)

        async def _subscribe_and_collect():
            count = 0
            async for evt in self.legacy_bus.subscribe("s1", replay_all=True):
                received.append(evt.event_type)
                count += 1
                if count >= 3:
                    break

        async def _run():
            task = loop.create_task(_subscribe_and_collect())
            await asyncio.sleep(0.05)
            # Emit via V2
            self.v2.emit("s1", "message_received", {})
            self.v2.emit("s1", "text_delta", {"text": "a"})
            self.v2.emit("s1", "agent_done", {})
            await task

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

        self.assertGreaterEqual(len(received), 3)

    def test_compact_handler_uses_v2_bus(self) -> None:
        """The /compact command handler uses service.event_bus.emit().

        Since V2 has the same emit() signature, this should work.
        This test simulates what _handle_compact_command does.
        """
        # Simulate the compact handler's event emission pattern
        compact_text_id = str(uuid.uuid4())
        self.v2.emit("s1", "message_received", {
            "user_message_id": "u-compact",
            "assistant_message_id": "a-compact",
            "status": "done",
        })
        self.v2.emit("s1", "text.started", {
            "text_id": compact_text_id,
            "message_id": "a-compact",
        })
        self.v2.emit("s1", "text_delta", {
            "text": "compressed!",
            "text_id": compact_text_id,
            "message_id": "a-compact",
        })
        self.v2.emit("s1", "text.ended", {
            "text_id": compact_text_id,
            "text": "compressed!",
            "message_id": "a-compact",
        })
        self.v2.emit("s1", "agent_done", {
            "message_id": "a-compact",
            "status": "completed",
        })

        # All 5 events in event_log
        self.assertGreaterEqual(self.v2.count("s1"), 5)

        # Projector can build the state
        state = self.proj.project("s1")
        # Should have at least the assistant message
        self.assertGreaterEqual(len(state.messages), 1)

        # Legacy bus also has all events
        legacy = self.legacy_bus.replay("s1", replay_all=True)
        self.assertGreaterEqual(len(legacy), 5)

    def test_multiple_sessions_isolated(self) -> None:
        """Events from different sessions don't interfere."""
        s1_events_before = self.v2.count("s1")

        # Create another session
        s2 = str(uuid.uuid4())
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (s2, "u", "s2", 1.0, 1.0),
        )
        conn.commit()
        conn.close()

        # Emit events on s2
        for i in range(10):
            self.v2.emit(s2, "text_delta", {"text": f"c{i}"})

        # s1 count unchanged
        s1_events_after = self.v2.count("s1")
        self.assertEqual(s1_events_before, s1_events_after)

        # s2 has 10 events
        self.assertEqual(self.v2.count(s2), 10)

        # Legacy bus s1 buffer unchanged
        s1_legacy = self.legacy_bus.replay("s1", replay_all=True)
        self.assertEqual(len(s1_legacy), s1_events_after)

    def test_v2_event_log_independent_of_legacy(self) -> None:
        """event_log persists independently of legacy bus state.

        If the legacy bus is cleared or crashes, event_log still
        has all events. This is the whole point of the dual-write:
        event_log is the durable source of truth.
        """
        # Emit some events
        for i in range(5):
            self.v2.emit("s1", "text_delta", {"text": str(i)})

        count_in_log = self.v2.count("s1")
        self.assertEqual(count_in_log, 5)

        # Clear legacy bus buffers (simulates process restart)
        self.legacy_bus._buffers.clear()

        # event_log still has all events
        self.assertEqual(self.v2.count("s1"), 5)

        # Can replay from event_log
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 5)

    def test_event_order_preserved(self) -> None:
        """Event order is the same in both event_log and legacy bus."""
        types = [
            "message_received",
            "attempt.created",
            "text.started",
            "text_delta",
            "text.ended",
            "tool_call",
            "tool_result",
            "assistant_message",
            "agent_done",
        ]
        for t in types:
            self.v2.emit("s1", t, {})

        # V2 order
        v2_events = self.v2.replay("s1")
        v2_types = [e.type for e in v2_events if e.type in types]

        # Legacy order
        legacy_events = self.legacy_bus.replay("s1", replay_all=True)
        legacy_types = [e.event_type for e in legacy_events if e.event_type in types]

        # Same order
        self.assertEqual(v2_types, types)
        self.assertEqual(legacy_types, types)


if __name__ == "__main__":
    unittest.main()
