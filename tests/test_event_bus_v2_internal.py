"""Tests for EventBusV2 internal methods.

These tests verify private methods:
- _should_flush: boundary event detection
- _next_seq: monotonic sequence generation
- _persist: error handling (IntegrityError, OperationalError, TypeError)
- _forward: SSEEvent creation
- _flush_projection: error handling
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.event_v2 import EventV2
from strategy_research.api.session.events import EventBus, SSEEvent


def _setup_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
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
        conn.execute("INSERT INTO sessions (id) VALUES (?)", ("s1",))
        conn.execute("INSERT INTO sessions (id) VALUES (?)", ("s2",))
        conn.commit()
    finally:
        conn.close()


def _make_event(aggregate_id="s1", seq=1, event_type="message_received", data=None) -> EventV2:
    return EventV2(id=f"evt_{aggregate_id}_{seq:04d}", aggregate_id=aggregate_id, seq=seq,
                  type=event_type, data=data or {}, time_created=1000.0 + seq)


def _add_event(conn, event: EventV2) -> None:
    conn.execute(
        "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, time_created) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event.id, event.aggregate_id, event.seq, event.type,
         "{}", event.time_created),
    )

class TestEventBusV2ShouldFlush(unittest.TestCase):
    """Verify _should_flush boundary detection."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.v2 = EventBusV2(EventBus(), self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_should_flush_message_received(self) -> None:
        self.assertTrue(self.v2._should_flush("message_received"))

    def test_should_flush_assistant_message(self) -> None:
        self.assertTrue(self.v2._should_flush("assistant_message"))

    def test_should_flush_compact(self) -> None:
        self.assertTrue(self.v2._should_flush("compact"))

    def test_should_flush_compact_ended(self) -> None:
        self.assertTrue(self.v2._should_flush("compact.ended"))

    def test_should_flush_iter_start(self) -> None:
        # Each LLM iteration boundary persists in-flight responses so a
        # refresh mid-run still shows completed iterations.
        self.assertTrue(self.v2._should_flush("iter_start"))

    def test_should_not_flush_text_delta(self) -> None:
        self.assertFalse(self.v2._should_flush("text_delta"))

    def test_should_not_flush_tool_progress(self) -> None:
        self.assertFalse(self.v2._should_flush("tool_progress"))

    def test_should_not_flush_text_started(self) -> None:
        self.assertFalse(self.v2._should_flush("text.started"))

    def test_should_not_flush_tool_call(self) -> None:
        self.assertFalse(self.v2._should_flush("tool_call"))

    def test_should_not_flush_unknown(self) -> None:
        self.assertFalse(self.v2._should_flush("unknown_event"))

    def test_should_not_flush_thinking_events(self) -> None:
        self.assertFalse(self.v2._should_flush("thinking_delta"))


class TestEventBusV2NextSeq(unittest.TestCase):
    """Verify _next_seq returns correct sequence numbers."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.v2 = EventBusV2(EventBus(), self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_next_seq_starts_at_1(self) -> None:
        self.assertEqual(self.v2._next_seq("s1"), 1)

    def test_next_seq_increments(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        _add_event(conn, _make_event(seq=1))
        conn.commit()
        conn.close()
        self.assertEqual(self.v2._next_seq("s1"), 2)

    def test_next_seq_isolation_per_session(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        _add_event(conn, _make_event(seq=1))
        _add_event(conn, _make_event(aggregate_id="s2", seq=1))
        conn.commit()
        conn.close()
        self.assertEqual(self.v2._next_seq("s1"), 2)
        self.assertEqual(self.v2._next_seq("s2"), 2)

    def test_next_seq_after_many_events(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        for i in range(1, 11):
            _add_event(conn, _make_event(seq=i))
        conn.commit()
        conn.close()
        self.assertEqual(self.v2._next_seq("s1"), 11)


class TestEventBusV2Forward(unittest.TestCase):
    """Verify _forward creates SSEEvent correctly."""

    def setUp(self) -> None:
        import asyncio
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
        os.unlink(self.db_path)

    def test_forward_returns_sse_event(self) -> None:
        event = _make_event()
        result = self.v2._forward(event)
        self.assertIsInstance(result, SSEEvent)
        self.assertEqual(result.event_id, event.id)
        self.assertEqual(result.event_type, event.type)
        self.assertEqual(result.data, event.data)
        self.assertEqual(result.session_id, event.aggregate_id)

    def test_forward_publishes_to_bus(self) -> None:
        event = _make_event()
        self.v2._forward(event)
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0].event_id, event.id)


class TestEventBusV2PersistErrors(unittest.TestCase):
    """Verify _persist error handling paths."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_persist_handles_seq_collision(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        _add_event(conn, _make_event(seq=1))
        conn.commit()
        conn.close()
        with self.assertLogs(level="ERROR") as logs:
            self.v2._persist(_make_event(seq=1))
        self.assertTrue(any("seq collision" in m for m in logs.output))

    def test_persist_handles_operational_error(self) -> None:
        bad_path = Path("/nonexistent/dir/db.db")
        v2 = EventBusV2(EventBus(), bad_path)
        with self.assertLogs(level="ERROR") as logs:
            v2._persist(_make_event())
        self.assertTrue(any("DB error" in m for m in logs.output) or
                any("OperationalError" in m for m in logs.output))

    def test_persist_handles_serialization_error(self) -> None:
        class BadObj:
            pass
        event = _make_event(data={"bad": BadObj()})
        with self.assertLogs(level="ERROR") as logs:
            self.v2._persist(event)
        self.assertTrue(any("not serializable" in m for m in logs.output))


class TestEventBusV2FlushProjection(unittest.TestCase):
    """Verify _flush_projection error handling."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path, flush_to_messages=True)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_flush_projection_handles_missing_tables(self) -> None:
        with self.assertLogs(level="ERROR") as logs:
            self.v2._flush_projection("s1")
        self.assertTrue(any("flush failed" in m for m in logs.output) or
                any("no such table" in m for m in logs.output))

    def test_flush_projection_empty_session(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT)")
        conn.execute("CREATE TABLE message_parts (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT)")
        conn.commit()
        conn.close()
        self.v2._flush_projection("s1")

    def test_flush_projection_with_events(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at REAL, message_type TEXT, seq INTEGER, metadata_json TEXT)")
        conn.execute("CREATE TABLE message_parts (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, type TEXT, data_json TEXT, seq INTEGER, time_created REAL)")
        conn.commit()
        conn.close()
        self.v2.publish(_make_event(seq=1, data={"message_id": "m1", "content": "hello"}))
        self.v2._flush_projection("s1")


class TestEventBusV2Count(unittest.TestCase):
    """Verify event count method."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_count_zero_for_empty(self) -> None:
        self.assertEqual(self.v2.count(), 0)

    def test_count_after_insert(self) -> None:
        self.v2.publish(_make_event(seq=1))
        self.assertEqual(self.v2.count(), 1)

    def test_count_by_session(self) -> None:
        self.v2.publish(_make_event(seq=1))
        self.v2.publish(_make_event(aggregate_id="s2", seq=1))
        self.assertEqual(self.v2.count("s1"), 1)
        self.assertEqual(self.v2.count("s2"), 1)

    def test_count_returns_zero_for_missing_db(self) -> None:
        v2 = EventBusV2(EventBus(), Path("/nonexistent/db.db"))
        self.assertEqual(v2.count(), 0)


class TestEventBusV2LastSeq(unittest.TestCase):
    """Verify last_seq method."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_last_seq_zero_for_empty(self) -> None:
        self.assertEqual(self.v2.last_seq("s1"), 0)

    def test_last_seq_after_insert(self) -> None:
        self.v2.publish(_make_event(seq=1))
        self.assertEqual(self.v2.last_seq("s1"), 1)

    def test_last_seq_returns_zero_for_missing_db(self) -> None:
        v2 = EventBusV2(EventBus(), Path("/nonexistent/db.db"))
        self.assertEqual(v2.last_seq("s1"), 0)


class TestEventBusV2Replay(unittest.TestCase):
    """Verify replay method."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_replay_empty_returns_empty(self) -> None:
        self.assertEqual(self.v2.replay("s1"), [])

    def test_replay_returns_events_in_order(self) -> None:
        self.v2.publish(_make_event(seq=1, data={"message_id": "m1"}))
        self.v2.publish(_make_event(seq=2, data={"message_id": "m2"}))
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].seq, 1)
        self.assertEqual(events[1].seq, 2)

    def test_replay_after_seq(self) -> None:
        self.v2.publish(_make_event(seq=1, data={"message_id": "m1"}))
        self.v2.publish(_make_event(seq=2, data={"message_id": "m2"}))
        events = self.v2.replay("s1", after_seq=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].seq, 2)

    def test_replay_limit(self) -> None:
        for i in range(1, 6):
            self.v2.publish(_make_event(seq=i, data={"message_id": f"m{i}"}))
        events = self.v2.replay("s1", limit=3)
        self.assertEqual(len(events), 3)

    def test_replay_returns_empty_for_missing_db(self) -> None:
        v2 = EventBusV2(EventBus(), Path("/nonexistent/db.db"))
        self.assertEqual(v2.replay("s1"), [])

    def test_replay_session_isolation(self) -> None:
        self.v2.publish(_make_event(seq=1, data={"message_id": "m1"}))
        self.v2.publish(_make_event(aggregate_id="s2", seq=1, data={"message_id": "m1"}))
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
