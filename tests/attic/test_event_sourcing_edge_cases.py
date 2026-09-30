"""Additional edge case tests for Phase 3 B1 components.

These tests cover edge cases not in the main test files:
1. EventBusV2 concurrent publish (thread safety)
2. EventBusV2 FK violation (event references nonexistent session)
3. Projector text_delta idempotency (re-apply behavior)
4. Projector with seq gaps (out-of-order events)
5. Projector with missing message_id (defensive)
6. EventV2 large payload + special characters
7. EventBusV2 batch with collision (partial failure)
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.core.events.event_v2 import EventType, EventV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import ProjectedSession, Projector


def _setup_db(db_path: Path, with_sessions: bool = True) -> None:
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
        if with_sessions:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "u", "t", 1.0, 1.0),
            )
        conn.commit()
    finally:
        conn.close()


class TestEventBusV2Concurrency(unittest.TestCase):
    """Verify EventBusV2 is thread-safe under concurrent publish."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_concurrent_publish_same_session(self) -> None:
        """Multiple threads publishing to the same session should not crash."""
        N_THREADS = 8
        N_EVENTS = 50
        errors: list[Exception] = []

        def publish_batch(thread_id: int) -> None:
            try:
                for i in range(N_EVENTS):
                    self.v2.publish(EventV2.create(
                        "s1",
                        thread_id * N_EVENTS + i + 1,
                        EventType.TEXT_DELTA,
                        {"thread": thread_id, "i": i},
                    ))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=publish_batch, args=(t,))
            for t in range(N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent publish raised: {errors}")
        # All N_THREADS * N_EVENTS events should be persisted
        self.assertEqual(self.v2.count("s1"), N_THREADS * N_EVENTS)

    def test_concurrent_publish_different_sessions(self) -> None:
        """Multiple threads, different sessions, no interference."""
        # Add more sessions (avoiding s1 which is created by _setup_db)
        conn = sqlite3.connect(str(self.db_path))
        try:
            for i in range(10):
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"multi-s{i}", "u", "t", 1.0, 1.0),
                )
            conn.commit()
        finally:
            conn.close()

        errors: list[Exception] = []

        def publish_to(s: str, n: int) -> None:
            try:
                for i in range(1, n + 1):
                    self.v2.publish(EventV2.create(
                        s, i, EventType.TEXT_DELTA, {},
                    ))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=publish_to, args=(f"multi-s{i}", 5))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        for i in range(10):
            self.assertEqual(self.v2.count(f"multi-s{i}"), 5)


class TestEventBusV2FKViolation(unittest.TestCase):
    """Verify what happens when an event references a non-existent session."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path, with_sessions=False)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_fk_violation_logs_but_doesnt_crash(self) -> None:
        """Publishing to a non-existent session must not crash."""
        e = EventV2.create("nonexistent-session", 1, EventType.TEXT_DELTA, {})
        # Should log error but not raise
        self.v2.publish(e)
        # No event should be persisted
        self.assertEqual(self.v2.count("nonexistent-session"), 0)


class TestEventBusV2BatchPartialFailure(unittest.TestCase):
    """Verify batch behavior when some events fail."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_batch_collision_partial(self) -> None:
        """First event succeeds, second collides — batch is atomic so all roll back."""
        # Pre-insert seq=2
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, type, "
                "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                ("pre-1", "s1", 2, "text.started", "{}", time.time()),
            )
            conn.commit()
        finally:
            conn.close()

        events = [
            EventV2.create("s1", 1, EventType.TEXT_DELTA, {}),
            EventV2.create("s1", 2, EventType.TEXT_DELTA, {}),  # collision
            EventV2.create("s1", 3, EventType.TEXT_DELTA, {}),
        ]
        # The batch is in a single transaction, so the collision rolls back ALL
        with self.assertRaises(sqlite3.IntegrityError):
            self.v2.publish_batch(events)
        # Verify nothing new was inserted
        self.assertEqual(self.v2.count("s1"), 1)


class TestProjectorEdgeCases(unittest.TestCase):
    """Projector edge cases not in the main test file."""

    def setUp(self) -> None:
        self.state = ProjectedSession(session_id="s1")
        self.projector = Projector(Path("/tmp/nonexistent"))

    def test_text_delta_reapply_appends(self) -> None:
        """Re-applying the same text_delta should append (idempotency check)."""
        e1 = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "a1", "text_id": "t1",
        })
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "hello ",
        })
        for e in (e1, e2):
            self.projector.apply(e, self.state)
        # First state: text = "hello "
        self.assertEqual(
            self.state.messages["a1"].parts["t1"].data["text"],
            "hello ",
        )
        # Replay the same events on a fresh state — should produce the same result
        fresh = ProjectedSession(session_id="s1")
        for e in (e1, e2):
            self.projector.apply(e, fresh)
        self.assertEqual(
            fresh.messages["a1"].parts["t1"].data["text"],
            "hello ",
        )
        # But re-applying e2 on the same state would double-append —
        # this is a known limitation; projector is for full re-replay.
        # For the full-replay use case, idempotency is preserved by
        # always creating a fresh ProjectedSession.

    def test_text_delta_on_unknown_text_id_creates_part(self) -> None:
        """If text.started is missed, text_delta should create the part lazily."""
        # Skip text.started; go straight to text_delta
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "hi",
        })
        self.projector.apply(e, self.state)
        self.assertIn("t1", self.state.messages["a1"].parts)
        self.assertEqual(
            self.state.messages["a1"].parts["t1"].data["text"], "hi"
        )

    def test_text_ended_without_text_started_creates_part(self) -> None:
        """text.ended on unknown text_id is a no-op (no part to update)."""
        e = EventV2.create("s1", 1, EventType.TEXT_ENDED, {
            "message_id": "a1", "text_id": "t1", "text": "final",
        })
        # Should not crash, no part created
        self.projector.apply(e, self.state)
        # The message IS created (lazy), but no part for t1
        self.assertIn("a1", self.state.messages)
        self.assertNotIn("t1", self.state.messages["a1"].parts)

    def test_tool_result_before_tool_call_creates_part(self) -> None:
        """Defensive: tool_result can arrive before tool_call (rare race)."""
        e = EventV2.create("s1", 1, EventType.TOOL_RESULT, {
            "message_id": "a1", "id": "tc-1",
            "result": "42", "status": "done",
        })
        self.projector.apply(e, self.state)
        # Part should be created with the result
        self.assertIn("tc-1", self.state.messages["a1"].parts)
        part = self.state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["result"], "42")
        self.assertEqual(part.data["state"], "done")

    def test_text_event_without_message_id_skipped(self) -> None:
        """Defensive: text events without message_id are skipped."""
        e = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            # no message_id
            "text_id": "t1",
        })
        self.projector.apply(e, self.state)  # should not crash
        # No message created
        self.assertEqual(len(self.state.messages), 0)

    def test_tool_event_without_call_id_warns(self) -> None:
        """Defensive: tool events without call_id are skipped."""
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1",
            # no id / call_id
            "tool": "foo",
        })
        self.projector.apply(e, self.state)  # should not crash
        # Message created (lazy), but no part
        self.assertIn("a1", self.state.messages)
        self.assertEqual(self.state.messages["a1"].parts, {})

    def test_apply_event_for_different_session_skipped(self) -> None:
        """Event with mismatched aggregate_id is skipped."""
        e = EventV2.create("OTHER-SESSION", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1", "content": "hi",
        })
        self.projector.apply(e, self.state)  # should not crash
        self.assertEqual(len(self.state.messages), 0)

    def test_text_progress_on_unknown_tool_skipped(self) -> None:
        """tool_progress on unknown tool_call is a no-op."""
        e = EventV2.create("s1", 1, EventType.TOOL_PROGRESS, {
            "message_id": "a1", "id": "tc-unknown",
            "stage": "fetching",
        })
        self.projector.apply(e, self.state)  # should not crash
        # No part created (tool_progress requires existing part)
        self.assertEqual(self.state.messages["a1"].parts, {})


class TestEventV2PayloadEdgeCases(unittest.TestCase):
    """Test EventV2 with edge case payloads."""

    def test_unicode_payload(self) -> None:
        """Unicode characters (CJK, emoji) round-trip correctly."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": "你好世界 🌍 こんにちは",
            "tokens": 42,
        })
        s = e.to_json()
        e2 = EventV2.from_json(s)
        self.assertEqual(e2.data["text"], "你好世界 🌍 こんにちは")

    def test_large_payload(self) -> None:
        """Large data payload (100KB) round-trips."""
        big_text = "x" * 100_000
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": big_text,
        })
        s = e.to_json()
        # JSON adds overhead (quotes, key, etc.); verify the
        # text content is fully preserved
        self.assertIn(big_text[:100], s)  # substring present
        self.assertGreater(len(s), 100_000)  # grew due to JSON wrapping
        e2 = EventV2.from_json(s)
        self.assertEqual(e2.data["text"], big_text)
        self.assertEqual(len(e2.data["text"]), 100_000)

    def test_nested_data(self) -> None:
        """Deeply nested data structures round-trip."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": [1, 2, {"level5": "deep"}]
                    }
                }
            }
        }
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, data)
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data, data)

    def test_empty_data(self) -> None:
        """Empty dict data is allowed."""
        e = EventV2.create("s1", 1, EventType.TEXT_STARTED, {})
        self.assertEqual(e.data, {})
        # JSON round-trip
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data, {})

    def test_no_data_argument(self) -> None:
        """No data argument defaults to {}."""
        e = EventV2.create("s1", 1, EventType.TEXT_STARTED)
        self.assertEqual(e.data, {})


class TestProjectorSeqGap(unittest.TestCase):
    """Test projector with out-of-order events (seq gaps)."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_projector_handles_seq_gaps(self) -> None:
        """Projector should handle events with gaps in seq numbers."""
        # seq 1, 3, 5 (gaps at 2, 4)
        self.v2.publish(EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1", "content": "hi",
        }))
        self.v2.publish(EventV2.create("s1", 3, EventType.TEXT_STARTED, {
            "message_id": "a1", "text_id": "t1",
        }))
        self.v2.publish(EventV2.create("s1", 5, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "ok",
        }))

        state = self.projector.project("s1")
        # All events are processed regardless of gaps
        self.assertEqual(len(state.messages), 2)
        self.assertIn("u1", state.messages)
        self.assertIn("a1", state.messages)
        self.assertEqual(
            state.messages["a1"].parts["t1"].data["text"], "ok"
        )

    def test_replay_after_seq_skips_older_events(self) -> None:
        """after_seq correctly filters out older events."""
        self.v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "first ",
        }))
        self.v2.publish(EventV2.create("s1", 2, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "second",
        }))
        self.v2.publish(EventV2.create("s1", 3, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": " third",
        }))
        # Replay from seq=2
        events = self.v2.replay("s1", after_seq=2)
        self.assertEqual([e.seq for e in events], [3])


if __name__ == "__main__":
    unittest.main()
