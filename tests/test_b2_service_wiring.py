"""B2 integration tests: EventBusV2 wired into service stack (Level 3, B2 commit 4).

These are NOT full E2E tests with real LLM calls. Instead, they verify:
1. EventBusV2 is correctly wired into SessionService
2. Service emits events that land in both event_log AND legacy EventBus
3. The projector can rebuild state from event_log after service operations
4. SSE backward compatibility is preserved

Uses direct SessionService + EventBusV2 instead of FastAPI TestClient,
to avoid needing LLM config.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import Projector
from strategy_research.api.session.service import SessionService
from strategy_research.api.session.store import SessionStore


def _setup_full_db(db_path: Path) -> None:
    """Create all tables needed for the full service stack."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT,
            archived INTEGER NOT NULL DEFAULT 0
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
        CREATE INDEX IF NOT EXISTS idx_messages_session_created
            ON messages(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_session_type_created
            ON messages(session_id, message_type, created_at);
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
        CREATE INDEX IF NOT EXISTS idx_message_parts_message_seq
            ON message_parts(message_id, seq);
        CREATE INDEX IF NOT EXISTS idx_message_parts_session_seq
            ON message_parts(session_id, seq);
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_seq
            ON event_log(aggregate_id, seq);
        """
    )
    conn.commit()
    conn.close()


class TestB2ServiceWiring(unittest.TestCase):
    """Verify EventBusV2 is correctly wired into SessionService."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_db(self.db_path)

        # Create session
        conn = sqlite3.connect(str(self.db_path))
        self.session_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.session_id, "admin", "Test", 1.0, 1.0),
        )
        conn.commit()
        conn.close()

        # Build the full stack: V2 bus wrapping legacy bus
        self.legacy_bus = EventBus()
        self.v2_bus = EventBusV2(self.legacy_bus, self.db_path)
        self.store = SessionStore(db_path=self.db_path)
        self.service = SessionService(store=self.store, event_bus=self.v2_bus)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_service_has_v2_bus(self) -> None:
        """SessionService.event_bus is an EventBusV2 instance."""
        self.assertIsInstance(self.service.event_bus, EventBusV2)
        # It wraps the legacy EventBus
        self.assertIs(self.service.event_bus.event_bus, self.legacy_bus)

    def test_create_session_emits_events(self) -> None:
        """create_session emits session.created event to both sinks."""
        sid = str(uuid.uuid4())
        result = self.service.create_session(sid, title="New Session")

        self.assertEqual(result.get("id"), sid)

        # Check event_log has the event
        self.assertGreater(self.v2_bus.count(sid), 0)

        # Check legacy bus has the event
        buffered = self.legacy_bus.replay(sid, replay_all=True)
        event_types = [e.event_type for e in buffered]
        self.assertIn("session.created", event_types)

    def test_append_message_triggers_no_events(self) -> None:
        """Direct DB message insert doesn't emit events (that's service's job).

        The DB is the persistence layer; events are emitted by the
        service layer. This test verifies the separation.
        """
        count_before = self.v2_bus.count(self.session_id)
        msg_id = str(uuid.uuid4())
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "created_at, message_type, seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, self.session_id, "user", "direct insert",
             1.0, "user", 1),
        )
        conn.commit()
        conn.close()
        count_after = self.v2_bus.count(self.session_id)
        # Direct DB operations don't emit events
        self.assertEqual(count_before, count_after)

    def test_event_log_grows_with_service_operations(self) -> None:
        """Each service operation that emits events grows event_log."""
        initial = self.v2_bus.count(self.session_id)

        # Create another session
        sid2 = str(uuid.uuid4())
        self.service.create_session(sid2, title="Second")
        after_create = self.v2_bus.count(sid2)
        self.assertGreater(after_create, 0)

        # Verify the event content
        events = self.v2_bus.replay(sid2)
        self.assertEqual(events[0].type, "session.created")
        self.assertEqual(events[0].data.get("title"), "Second")

    def test_event_ids_match_between_sinks(self) -> None:
        """Event IDs are the same in event_log and legacy bus.

        This is essential for Last-Event-ID replay to work seamlessly
        across both systems.
        """
        sid = str(uuid.uuid4())
        self.service.create_session(sid, title="ID Test")

        # Get from V2 (event_log)
        v2_events = self.v2_bus.replay(sid)
        # Get from legacy (in-memory buffer)
        legacy_events = self.legacy_bus.replay(sid, replay_all=True)

        self.assertGreater(len(v2_events), 0)
        self.assertGreater(len(legacy_events), 0)

        # Event IDs should match for the same event
        v2_ids = {e.id for e in v2_events}
        legacy_ids = {e.event_id for e in legacy_events}
        # All legacy event IDs should be in V2 (dual-write)
        self.assertTrue(legacy_ids.issubset(v2_ids))

    def test_projector_empty_session(self) -> None:
        """Projector returns empty state for session with no message events."""
        state = self.proj.project(self.session_id)
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 0)

    def test_projector_after_user_message(self) -> None:
        """Projector rebuilds user message from event_log."""
        # Simulate what service.send_message does:
        # 1. Insert message directly
        user_msg_id = str(uuid.uuid4())
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "created_at, message_type, seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_msg_id, self.session_id, "user", "hello projector",
             1.0, "user", 1),
        )
        conn.commit()
        conn.close()

        # 2. Emit message_received event (via V2 bus)
        self.v2_bus.emit(
            self.session_id,
            "message_received",
            {
                "message_id": user_msg_id,
                "user_message_id": user_msg_id,
                "content": "hello projector",
                "role": "user",
            },
        )

        # Project from event_log
        state = self.proj.project(self.session_id)
        self.assertEqual(len(state.messages), 1)
        msg = list(state.messages.values())[0]
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.content, "hello projector")

        # Verify against direct query
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_many_events_seq_monotonic(self) -> None:
        """All events in event_log have strictly increasing seq."""
        for i in range(50):
            self.v2_bus.emit(
                self.session_id,
                "text_delta",
                {"message_id": "a1", "text_id": "t1", "text": f"c{i}"},
            )

        events = self.v2_bus.replay(self.session_id)
        self.assertEqual(len(events), 50)
        seqs = [e.seq for e in events]
        # Strictly increasing
        self.assertEqual(seqs, list(range(1, 51)))

    def test_legacy_bus_subscribers_work(self) -> None:
        """Legacy EventBus subscribers still receive events via V2.

        Backward compat: any existing code that subscribes to the
        legacy EventBus continues to work after switching to V2.
        """
        received_types = []

        # Subscribe via legacy bus
        import asyncio

        async def _collect():
            async for evt in self.legacy_bus.subscribe(
                self.session_id, replay_all=True,
            ):
                received_types.append(evt.event_type)
                if len(received_types) >= 3:
                    break

        loop = asyncio.new_event_loop()
        self.legacy_bus.set_loop(loop)

        async def _run():
            task = loop.create_task(_collect())
            await asyncio.sleep(0.05)  # Let subscriber register
            # Emit events via V2
            self.v2_bus.emit(self.session_id, "message_received", {})
            self.v2_bus.emit(self.session_id, "text_delta", {"text": "hi"})
            self.v2_bus.emit(self.session_id, "agent_done", {"status": "ok"})
            await task

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

        # All 3 events should have been received via the legacy bus
        self.assertGreaterEqual(len(received_types), 3)
        self.assertIn("message_received", received_types)
        self.assertIn("text_delta", received_types)
        self.assertIn("agent_done", received_types)

    def test_v2_bus_has_same_emit_signature(self) -> None:
        """EventBusV2.emit() has the same signature as EventBus.emit().

        This is what makes the drop-in replacement work.
        """
        import inspect

        v2_sig = inspect.signature(self.v2_bus.emit)
        legacy_sig = inspect.signature(self.legacy_bus.emit)

        v2_params = list(v2_sig.parameters.keys())
        legacy_params = list(legacy_sig.parameters.keys())

        # Both accept session_id, event_type, data
        self.assertIn("session_id", v2_params)
        self.assertIn("event_type", v2_params)
        self.assertIn("data", v2_params)
        self.assertEqual(v2_params[:3], legacy_params[:3])

    def test_service_emit_return_value(self) -> None:
        """V2 emit returns SSEEvent, same as legacy emit.

        Some code paths check the return value of emit().
        """
        result = self.service.event_bus.emit(
            self.session_id, "test_event", {"key": "val"},
        )

        # Should be an SSEEvent-like object with event_type, data, etc.
        self.assertEqual(result.event_type, "test_event")
        self.assertEqual(result.data["key"], "val")
        self.assertEqual(result.session_id, self.session_id)


if __name__ == "__main__":
    unittest.main()
