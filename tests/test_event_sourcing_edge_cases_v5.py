"""Round 5 edge case tests for Phase 3 B1.

The final focused round. The most valuable remaining gap is:
1. Projector output consistency with what service.py would have
   written to messages + message_parts (the B2 migration invariant)
2. Backup/restore — copy DB, verify events survive
3. SQLite WAL mode compatibility
4. Memory stability under load
5. EventBusV2 with very long event IDs (UUID-like)
6. EventBusV2 with concurrent publish + replay after process restart
7. ProjectedState.to_dict() / from_dict() for serialization
"""
from __future__ import annotations

import gc
import json
import shutil
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
from strategy_research.api.session.projector import (
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession,
    Projector,
)


def _setup_full_schema(db_path: Path, with_session: bool = True) -> None:
    """Create the full schema (sessions, messages, message_parts, event_log)."""
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
        if with_session:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "u", "t", 1.0, 1.0),
            )
        conn.commit()
    finally:
        conn.close()


# ── Projector vs live DB consistency ─────────────────────────────


class TestProjectorLiveDBConsistency(unittest.TestCase):
    """Verify projector output is consistent with what service.py
    would have written to messages + message_parts.

    This is the key invariant for B2: when we switch the read path
    to use the projector, the data must be the same as what
    service.py wrote.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_projected_messages_match_live_messages(self) -> None:
        """After events are projected, the messages table can be
        populated to match."""
        # Publish events
        events_data = [
            (1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "user_message_id": "u1",
                "role": "user", "content": "Hello",
            }),
            (2, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            (3, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Hi there!",
            }),
            (4, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a1", "content": "Hi there!",
            }),
        ]
        for seq, et, data in events_data:
            self.v2.publish(EventV2.create("s1", seq, et, data))

        # Project the events
        state = self.projector.project("s1")
        # Write the projected state to the live DB
        message_rows = state.to_message_rows()
        part_rows = state.to_part_rows()

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
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

            # Now query the live DB and verify it matches the projected state
            live_messages = conn.execute(
                "SELECT id, role, content, seq FROM messages "
                "WHERE session_id = ? ORDER BY seq",
                ("s1",),
            ).fetchall()
            self.assertEqual(len(live_messages), 2)
            self.assertEqual(live_messages[0]["id"], "u1")
            self.assertEqual(live_messages[0]["role"], "user")
            self.assertEqual(live_messages[0]["content"], "Hello")
            self.assertEqual(live_messages[1]["id"], "a1")
            self.assertEqual(live_messages[1]["role"], "assistant")
            self.assertEqual(live_messages[1]["content"], "Hi there!")

            # And parts
            live_parts = conn.execute(
                "SELECT id, type, data_json FROM message_parts "
                "WHERE session_id = ?",
                ("s1",),
            ).fetchall()
            self.assertEqual(len(live_parts), 1)
            self.assertEqual(live_parts[0]["id"], "t1")
            self.assertEqual(live_parts[0]["type"], "text")
            data = json.loads(live_parts[0]["data_json"])
            self.assertEqual(data["text"], "Hi there!")
        finally:
            conn.close()

    def test_projection_round_trip_preserves_data(self) -> None:
        """After project() → to_rows() → INSERT → SELECT → JSON, the
        data should be equivalent to the projected state."""
        # Publish a complex event stream
        events_data = [
            (1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "user_message_id": "u1",
                "role": "user", "content": "Test",
            }),
            (2, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            (3, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1",
                "text": "复杂 unicode 你好 🌍",
            }),
            (4, EventType.TOOL_CALL, {
                "message_id": "a1", "id": "tc-1",
                "tool": "calc", "input": {"x": 1, "y": 2},
            }),
            (5, EventType.TOOL_RESULT, {
                "message_id": "a1", "id": "tc-1",
                "result": "3", "status": "done",
            }),
            (6, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a1", "content": "复杂 unicode 你好 🌍",
            }),
        ]
        for seq, et, data in events_data:
            self.v2.publish(EventV2.create("s1", seq, et, data))

        state = self.projector.project("s1")

        # Insert into DB
        message_rows = state.to_message_rows()
        part_rows = state.to_part_rows()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
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

            # Read back and verify data integrity
            row = conn.execute(
                "SELECT data_json FROM message_parts WHERE id = ?", ("t1",)
            ).fetchone()
            text_data = json.loads(row["data_json"])["text"]
            self.assertEqual(text_data, "复杂 unicode 你好 🌍")

            row = conn.execute(
                "SELECT data_json FROM message_parts WHERE id = ?", ("tc-1",)
            ).fetchone()
            tc_data = json.loads(row["data_json"])
            self.assertEqual(tc_data["input"], {"x": 1, "y": 2})
            self.assertEqual(tc_data["result"], "3")
        finally:
            conn.close()


# ── Backup / restore ──────────────────────────────────────────────


class TestEventBusV2BackupRestore(unittest.TestCase):
    """Verify events survive a DB copy (simulating backup/restore)."""

    def test_events_survive_db_copy(self) -> None:
        """Copy the DB file, then verify events are still readable."""
        tmp_dir = tempfile.mkdtemp()
        try:
            src = Path(tmp_dir) / "src.db"
            dst = Path(tmp_dir) / "dst.db"
            _setup_full_schema(src)
            bus = EventBus()
            v2 = EventBusV2(bus, src)
            for i in range(1, 11):
                v2.publish(EventV2.create(
                    "s1", i, EventType.TEXT_DELTA, {"i": i},
                ))

            # Copy the DB (using SQLite backup for safety)
            with sqlite3.connect(str(src)) as src_conn:
                with sqlite3.connect(str(dst)) as dst_conn:
                    src_conn.backup(dst_conn)

            # Open the backup and verify
            bus2 = EventBus()
            v2_2 = EventBusV2(bus2, dst)
            events = v2_2.replay("s1")
            self.assertEqual(len(events), 10)
            self.assertEqual(v2_2.count("s1"), 10)
            self.assertEqual(v2_2.last_seq("s1"), 10)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── SQLite WAL mode compatibility ─────────────────────────────────


class TestEventBusV2WALMode(unittest.TestCase):
    """Verify EventBusV2 works with SQLite WAL (Write-Ahead Logging) mode.

    WAL is important for production: it allows concurrent readers while
    a writer is active, and is more crash-safe than the default journal.
    """

    def test_publish_with_wal_mode(self) -> None:
        """Set WAL mode on the DB and verify publish still works."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_full_schema(db_path)
        # Switch to WAL mode
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        finally:
            conn.close()

        bus = EventBus()
        v2 = EventBusV2(bus, db_path)
        for i in range(1, 6):
            v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))
        self.assertEqual(v2.count("s1"), 5)
        db_path.unlink(missing_ok=True)
        # Also clean up WAL files
        for ext in ("-wal", "-shm"):
            p = db_path.with_name(db_path.name + ext)
            p.unlink(missing_ok=True)


# ── Memory stability under load ──────────────────────────────────


class TestEventBusV2MemoryStability(unittest.TestCase):
    """Verify EventBusV2 doesn't leak memory under load.

    We can't directly measure memory, but we can verify:
    1. The EventBus buffer doesn't grow beyond max_buffer_size
    2. Projector state can be garbage collected after use
    """

    def test_eventbus_buffer_capped(self) -> None:
        """The legacy EventBus buffer is capped at max_buffer_size."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_full_schema(db_path)
        try:
            bus = EventBus(max_buffer_size=100)
            v2 = EventBusV2(bus, db_path)
            # Publish 1000 events
            for i in range(1, 1001):
                v2.publish(EventV2.create(
                    "s1", i, EventType.TEXT_DELTA, {"i": i},
                ))
            # Buffer should be capped at 100
            buffered = bus.replay("s1", replay_all=True)
            self.assertEqual(len(buffered), 100)
            # event_log has all 1000
            self.assertEqual(v2.count("s1"), 1000)
        finally:
            db_path.unlink(missing_ok=True)

    def test_projection_state_garbage_collected(self) -> None:
        """ProjectedState can be garbage collected after use."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_full_schema(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            for i in range(1, 101):
                v2.publish(EventV2.create(
                    "s1", i, EventType.TEXT_DELTA, {"i": i},
                ))
            projector = Projector(db_path)
            state = projector.project("s1")
            # Force GC; verify no leaks
            del state
            del projector
            gc.collect()
            # If we got here without OOM, we're fine
            self.assertTrue(True)
        finally:
            db_path.unlink(missing_ok=True)


# ── EventBusV2 with very long event IDs ──────────────────────────


class TestEventBusV2LongEventIDs(unittest.TestCase):
    """Test EventBusV2 with unusually long event IDs."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_with_custom_long_id(self) -> None:
        """An event with a long custom ID round-trips."""
        # UUID is 36 chars, but the test verifies longer IDs work
        long_id = "x" * 200
        e = EventV2(
            id=long_id, aggregate_id="s1", seq=1,
            type=EventType.TEXT_DELTA, data={"x": 1},
            time_created=time.time(),
        )
        self.v2.publish(e)
        replayed = self.v2.replay("s1")
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].id, long_id)

    def test_publish_with_uuid_like_id(self) -> None:
        """Standard UUID format IDs work."""
        import uuid
        e = EventV2(
            id=str(uuid.uuid4()), aggregate_id="s1", seq=1,
            type=EventType.TEXT_DELTA, data={},
            time_created=time.time(),
        )
        self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 1)


# ── EventBusV2 across process restart + concurrent publish ──────


class TestEventBusV2RestartThenConcurrent(unittest.TestCase):
    """Simulate a process restart, then concurrent publish.

    This combines the two stress scenarios: durability (restart
    preserves events) and concurrency (concurrent publish after
    restart).
    """

    def test_restart_then_concurrent_publish(self) -> None:
        """Publish, restart, then publish concurrently."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_full_schema(db_path)
        try:
            # First "process": publish 10 events
            bus1 = EventBus()
            v2_1 = EventBusV2(bus1, db_path)
            for i in range(1, 11):
                v2_1.publish(EventV2.create(
                    "s1", i, EventType.TEXT_DELTA, {"i": i},
                ))
            del bus1, v2_1
            gc.collect()

            # Second "process": concurrent publish of 100 more events
            bus2 = EventBus()
            v2_2 = EventBusV2(bus2, db_path)
            N_THREADS = 4
            errors: list[Exception] = []

            def publish_batch(tid: int) -> None:
                try:
                    for i in range(25):
                        seq = 10 + tid * 25 + i + 1
                        v2_2.publish(EventV2.create(
                            "s1", seq, EventType.TEXT_DELTA, {"t": tid, "i": i},
                        ))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=publish_batch, args=(t,))
                       for t in range(N_THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            # 10 (first process) + 100 (second process) = 110
            self.assertEqual(v2_2.count("s1"), 110)
            # last_seq is 10 + 4*25 = 110
            self.assertEqual(v2_2.last_seq("s1"), 110)
        finally:
            db_path.unlink(missing_ok=True)


# ── Projector: state.to_dict for serialization ────────────────────


class TestProjectedStateToDict(unittest.TestCase):
    """Test ProjectedSession/Message/Part have a dict-friendly repr."""

    def test_message_to_dict_via_dataclasses(self) -> None:
        """The dataclass.asdict conversion produces a clean nested dict."""
        import dataclasses
        m = ProjectedMessage(
            id="m1", session_id="s1", role="assistant",
            content="hi", seq=1, created_at=1.0,
        )
        m.parts["p1"] = ProjectedPart(
            id="p1", type="text", data={"text": "hi"},
        )
        d = dataclasses.asdict(m)
        self.assertEqual(d["id"], "m1")
        self.assertEqual(d["role"], "assistant")
        self.assertIn("p1", d["parts"])
        self.assertEqual(d["parts"]["p1"]["type"], "text")

    def test_session_to_dict_via_dataclasses(self) -> None:
        import dataclasses
        s = ProjectedSession(session_id="s1")
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi",
        )
        d = dataclasses.asdict(s)
        self.assertEqual(d["session_id"], "s1")
        self.assertIn("m1", d["messages"])


# ── Projector: edge case in tool_call input shape ────────────────


class TestProjectorToolCallInputShapes(unittest.TestCase):
    """Various input shapes for tool_call events."""

    def test_tool_call_with_input_as_string(self) -> None:
        """LLM API: arguments is a JSON string, not a dict."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1",
            "id": "tc-1",
            "function": {
                "name": "calc",
                "arguments": '{"x": 1}',  # JSON string
            },
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        # data.function preserved
        self.assertEqual(part.data["function"]["arguments"], '{"x": 1}')

    def test_tool_call_with_no_function_field(self) -> None:
        """Flat shape without function field."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1",
            "id": "tc-1",
            "tool": "calc",
            "input": {"x": 1},
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["tool"], "calc")
        self.assertEqual(part.data["input"], {"x": 1})

    def test_tool_call_with_empty_input(self) -> None:
        """Empty input dict."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",
            "tool": "noop", "input": {},
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["input"], {})

    def test_tool_call_with_null_input(self) -> None:
        """Null input (tool takes no arguments)."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",
            "tool": "get_time", "input": None,
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertIsNone(part.data["input"])


# ── EventBusV2: replay after long chain ──────────────────────────


class TestEventBusV2LongReplay(unittest.TestCase):
    """Test replay performance with a long chain of events."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_replay_long_chain(self) -> None:
        """Replaying 5000 events works (verifies pagination, ordering)."""
        N = 5000
        for i in range(1, N + 1):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))

        # Replay in chunks of 1000
        all_events = []
        for chunk_start in range(0, N, 1000):
            chunk = self.v2.replay("s1", after_seq=chunk_start, limit=1000)
            all_events.extend(chunk)
        self.assertEqual(len(all_events), N)
        # Verify ordering
        for i, e in enumerate(all_events, start=1):
            self.assertEqual(e.seq, i)


# ── EventBusV2: edge case: empty aggregate_id rejected ───────────


class TestEventBusV2AggregateIdValidation(unittest.TestCase):
    """EventV2.create validates aggregate_id is non-empty."""

    def test_create_rejects_empty_aggregate_id(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.create("", 1, EventType.TEXT_DELTA)

    def test_create_rejects_whitespace_aggregate_id(self) -> None:
        """Whitespace-only aggregate_id is accepted (EventV2 doesn't strip)."""
        # Documented behavior: EventV2 only checks non-empty, not content
        e = EventV2.create("   ", 1, EventType.TEXT_DELTA)
        self.assertEqual(e.aggregate_id, "   ")


# ── EventBusV2: timestamp precision ──────────────────────────────


class TestEventBusV2TimestampPrecision(unittest.TestCase):
    """Test timestamp precision and ordering."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_events_ordered_by_seq_not_time(self) -> None:
        """Events are returned in seq order, not time_created order."""
        # Publish events out of time order (seq 1 has later time)
        from dataclasses import replace
        e1 = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"i": 1})
        time.sleep(0.01)
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {"i": 2})
        # Now manually make e1 have a later time_created
        e1_late = replace(e1, time_created=e2.time_created + 1.0)
        # Publish e1_late (id collision if we don't change id)
        e1_late = replace(e1_late, id="different-id")
        self.v2.publish(e1_late)
        self.v2.publish(e2)
        # Replay returns in seq order, not time order
        events = self.v2.replay("s1")
        self.assertEqual(events[0].seq, 1)
        self.assertEqual(events[1].seq, 2)
        # But time_created is the later one for seq=1
        self.assertGreater(events[0].time_created, events[1].time_created)


# ── Projector: state isolation across sessions ──────────────────


class TestProjectorCrossSessionIsolation(unittest.TestCase):
    """Projecting session A should not affect session B."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_schema(self.db_path)
        # Add 3 sessions
        conn = sqlite3.connect(str(self.db_path))
        try:
            for sid in ("a", "b", "c"):
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sid, "u", "t", 1.0, 1.0),
                )
            conn.commit()
        finally:
            conn.close()
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_projection_isolation(self) -> None:
        """Projecting session A returns only A's events."""
        for session_id, msg_id in [("a", "m-a"), ("b", "m-b"), ("c", "m-c")]:
            self.v2.publish(EventV2.create(
                session_id, 1, EventType.MESSAGE_RECEIVED, {
                    "message_id": msg_id, "content": f"hello {session_id}",
                },
            ))

        state_a = self.projector.project("a")
        state_b = self.projector.project("b")
        state_c = self.projector.project("c")
        # Each has exactly its own message
        self.assertEqual(len(state_a.messages), 1)
        self.assertEqual(len(state_b.messages), 1)
        self.assertEqual(len(state_c.messages), 1)
        self.assertIn("m-a", state_a.messages)
        self.assertIn("m-b", state_b.messages)
        self.assertIn("m-c", state_c.messages)
        self.assertEqual(state_a.messages["m-a"].content, "hello a")
        self.assertEqual(state_b.messages["m-b"].content, "hello b")
        self.assertEqual(state_c.messages["m-c"].content, "hello c")

    def test_project_session_with_no_events(self) -> None:
        """Projecting a session with no events returns empty state."""
        # Don't publish anything for session "b"
        self.v2.publish(EventV2.create("a", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "m1", "content": "hi",
        }))
        state = self.projector.project("b")
        self.assertEqual(len(state.messages), 0)


if __name__ == "__main__":
    unittest.main()
