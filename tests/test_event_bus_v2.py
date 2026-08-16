"""Tests for EventBusV2 dual-write (Level 3, B1 commit 3).

EventBusV2 publishes events to TWO sinks:
1. event_log table (persistence)
2. Legacy EventBus (live SSE)

These tests verify:
- Persist behavior: events land in event_log with correct schema
- Forward behavior: events reach EventBus subscribers
- Replay: events can be read back from event_log in seq order
- last_seq: returns max seq for resume-after-disconnect
- count: returns row count
- Batch publish: multiple events in one transaction
- Error handling: UNIQUE collision logs but doesn't crash
- Forward-compat: unknown event types persist but warn
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.core.events.event_v2 import EventType, EventV2
from strategy_research.api.session.events import EventBus


def _setup_db(db_path: Path) -> None:
    """Create event_log + sessions tables on a fresh DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute(
            """
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
            """
        )
        conn.execute(
            "CREATE INDEX idx_event_log_aggregate_seq "
            "ON event_log(aggregate_id, seq)"
        )
        conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s2",)
        )
        conn.commit()
    finally:
        conn.close()


class TestEventBusV2Persist(unittest.TestCase):
    """Verify event_log persistence."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_persists_to_event_log(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_STARTED, {"text_id": "x"})
        self.v2.publish(e)

        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT id, aggregate_id, seq, type, data_json "
                "FROM event_log WHERE id = ?",
                (e.id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "s1")
        self.assertEqual(rows[0][2], 1)
        self.assertEqual(rows[0][3], "text.started")
        import json
        self.assertEqual(json.loads(rows[0][4]), {"text_id": "x"})

    def test_publish_increments_event_log(self) -> None:
        for i in range(1, 6):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {"i": i}))
        self.assertEqual(self.v2.count("s1"), 5)

    def test_publish_collision_logs_but_doesnt_crash(self) -> None:
        """Same (aggregate_id, seq) must not crash publish()."""
        e1 = EventV2.create("s1", 1, EventType.TEXT_DELTA)
        e2 = EventV2.create("s1", 1, EventType.TEXT_DELTA)  # collision
        self.v2.publish(e1)  # OK
        # Second publish with same seq must be handled (log + skip)
        self.v2.publish(e2)  # Should not raise
        # Only e1 is in event_log
        self.assertEqual(self.v2.count("s1"), 1)

    def test_publish_unknown_type_persists(self) -> None:
        """Forward-compat: unknown types still persist."""
        e = EventV2.create("s1", 1, "future.event.type", {"x": 1})
        self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 1)


class TestEventBusV2Forward(unittest.TestCase):
    """Verify SSE forwarding via legacy EventBus."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.loop = asyncio.new_event_loop()
        self.bus.set_loop(self.loop)
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.loop.close()
        self.db_path.unlink(missing_ok=True)

    def test_publish_forwards_to_legacy_eventbus(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"text": "hi"})
        self.v2.publish(e)
        # Legacy EventBus buffer should have the event
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0].event_id, e.id)
        self.assertEqual(buffered[0].event_type, "text_delta")
        self.assertEqual(buffered[0].data, {"text": "hi"})

    def test_publish_uses_event_id_from_envelope(self) -> None:
        """SSE event_id must match event_log id for replay correlation."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
        self.v2.publish(e)
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(buffered[0].event_id, e.id)

    def test_publish_session_id_from_aggregate_id(self) -> None:
        e = EventV2.create("my-session-id", 1, EventType.TEXT_DELTA, {})
        self.v2.publish(e)
        buffered = self.bus.replay("my-session-id", replay_all=True)
        self.assertEqual(len(buffered), 1)


class TestEventBusV2Replay(unittest.TestCase):
    """Verify event_log → EventV2 replay."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_replay_returns_all_events(self) -> None:
        for i in range(1, 4):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {"i": i}))
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 3)
        self.assertEqual([e.seq for e in events], [1, 2, 3])

    def test_replay_after_seq(self) -> None:
        for i in range(1, 6):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {}))
        events = self.v2.replay("s1", after_seq=3)
        self.assertEqual([e.seq for e in events], [4, 5])

    def test_replay_with_limit(self) -> None:
        for i in range(1, 6):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {}))
        events = self.v2.replay("s1", limit=2)
        self.assertEqual(len(events), 2)
        self.assertEqual([e.seq for e in events], [1, 2])

    def test_replay_empty_session(self) -> None:
        events = self.v2.replay("nonexistent")
        self.assertEqual(events, [])

    def test_replay_preserves_data_payload(self) -> None:
        payload = {"text": "你好", "tokens": 42, "nested": {"k": "v"}}
        self.v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, payload))
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, payload)

    def test_replay_isolates_sessions(self) -> None:
        self.v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {}))
        self.v2.publish(EventV2.create("s1", 2, EventType.TEXT_DELTA, {}))
        self.v2.publish(EventV2.create("s2", 1, EventType.TEXT_DELTA, {}))
        self.assertEqual(len(self.v2.replay("s1")), 2)
        self.assertEqual(len(self.v2.replay("s2")), 1)


class TestEventBusV2Counters(unittest.TestCase):
    """Verify last_seq and count helpers."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_last_seq_empty(self) -> None:
        self.assertEqual(self.v2.last_seq("s1"), 0)

    def test_last_seq_returns_max(self) -> None:
        for i in [3, 1, 5, 2, 4]:  # out of order
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {}))
        self.assertEqual(self.v2.last_seq("s1"), 5)

    def test_last_seq_per_session(self) -> None:
        self.v2.publish(EventV2.create("s1", 10, EventType.TEXT_DELTA, {}))
        self.v2.publish(EventV2.create("s2", 5, EventType.TEXT_DELTA, {}))
        self.assertEqual(self.v2.last_seq("s1"), 10)
        self.assertEqual(self.v2.last_seq("s2"), 5)

    def test_count_total(self) -> None:
        for i in range(1, 4):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {}))
        for i in range(1, 3):
            self.v2.publish(EventV2.create("s2", i, EventType.TEXT_DELTA, {}))
        self.assertEqual(self.v2.count(), 5)
        self.assertEqual(self.v2.count("s1"), 3)
        self.assertEqual(self.v2.count("s2"), 2)

    def test_count_empty(self) -> None:
        self.assertEqual(self.v2.count(), 0)
        self.assertEqual(self.v2.count("s1"), 0)


class TestEventBusV2Batch(unittest.TestCase):
    """Verify batch publish in a single transaction."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_batch_persists_all(self) -> None:
        events = [
            EventV2.create("s1", i, EventType.TEXT_DELTA, {"i": i})
            for i in range(1, 4)
        ]
        self.v2.publish_batch(events)
        self.assertEqual(self.v2.count("s1"), 3)

    def test_publish_batch_forwards_all(self) -> None:
        events = [
            EventV2.create("s1", i, EventType.TEXT_DELTA, {"i": i})
            for i in range(1, 4)
        ]
        self.v2.publish_batch(events)
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 3)

    def test_publish_batch_empty_list(self) -> None:
        # Should be a no-op, not crash
        self.v2.publish_batch([])
        self.assertEqual(self.v2.count("s1"), 0)


class TestEventBusV2IntegrationWithEnsureSchema(unittest.TestCase):
    """End-to-end test: use the real _ensure_schema in web_session.py."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        import strategy_research.api.routers.web_session as ws
        self._orig = ws._get_db_path
        ws._get_db_path = lambda: self.db_path
        # Run ensure_schema
        with ws._get_db():
            pass

    def tearDown(self) -> None:
        import strategy_research.api.routers.web_session as ws
        ws._get_db_path = self._orig
        self.db_path.unlink(missing_ok=True)

    def test_eventbusv2_works_against_real_schema(self) -> None:
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path)

        # The DB now has a real sessions table (from _ensure_schema)
        e = EventV2.create("any-session-id", 1, EventType.TEXT_DELTA, {"x": 1})
        v2.publish(e)
        # The publish call may have hit a FK violation if the session
        # doesn't exist. That's actually a useful invariant — but for
        # this test, we just verify the call doesn't crash.
        # To exercise the happy path, create the session first.
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?)",
                ("any-session-id", "u1", "t", time.time(), time.time()),
            )
            conn.commit()
        finally:
            conn.close()
        e2 = EventV2.create("any-session-id", 1, EventType.TEXT_DELTA, {"x": 2})
        v2.publish(e2)
        self.assertEqual(v2.count("any-session-id"), 1)


if __name__ == "__main__":
    unittest.main()
