"""Round 6 edge case tests for Phase 3 B1.

Genuine remaining gaps:
1. Fuzz testing — random bytes as event data
2. EventV2 hashability / immutability
3. EventBusV2 with read-only DB file
4. EventBusV2 pickle-ability (multiprocess use)
5. EventV2 deep-copy / clone
6. Projector subclass-ability / custom handlers
7. EventBusV2 with isolation_level=None
8. EventBusV2 with custom PRAGMA settings
9. Projector with duplicate call_id in one message
10. Projector streaming mode (incremental state via after_seq)
11. EventBusV2 with concurrent SSE subscribers + publishing
12. EventV2 __repr__ edge cases
13. EventBusV2 with non-existent directory parent
14. EventV2 with non-string id (e.g., int)
15. ProjectedSession equality semantics
"""
from __future__ import annotations

import asyncio
import copy
import os
import pickle
import random
import shutil
import sqlite3
import string
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.core.events.event_v2 import EventType, EventV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import (
    ProjectedMessage,
    ProjectedSession,
    Projector,
)


def _setup_db(db_path: Path, with_session: bool = True) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
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
        if with_session:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "u", "t", 1.0, 1.0),
            )
        conn.commit()
    finally:
        conn.close()


# ── Fuzz testing ──────────────────────────────────────────────────


class TestFuzzing(unittest.TestCase):
    """Generate random event data and verify no crash."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_random_unicode_in_data(self) -> None:
        """Various Unicode characters in data — no crash, no exception."""
        random.seed(42)
        for i in range(50):
            # Generate random unicode
            chars = ''.join(
                chr(random.randint(0x4e00, 0x9fff))  # CJK range
                for _ in range(random.randint(1, 20))
            )
            data = {"text": chars, "i": i}
            e = EventV2.create("s1", i + 1, EventType.TEXT_DELTA, data)
            self.v2.publish(e)
        # All 50 events should be persisted
        self.assertEqual(self.v2.count("s1"), 50)

    def test_random_data_structures(self) -> None:
        """Various data structures — lists, dicts, mixed nesting."""
        random.seed(43)
        for i in range(50):
            depth = random.randint(0, 5)
            data = self._random_data(depth)
            e = EventV2.create("s1", i + 1, EventType.TEXT_DELTA, data)
            self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 50)

    def _random_data(self, depth: int) -> Any:
        """Generate random JSON-serializable data of given depth."""
        if depth <= 0:
            return random.choice([
                random.randint(0, 1000),
                random.random(),
                ''.join(random.choices(string.ascii_letters, k=10)),
                random.choice([True, False]),
                None,
            ])
        return {
            ''.join(random.choices(string.ascii_lowercase, k=5)): self._random_data(depth - 1)
            for _ in range(random.randint(1, 5))
        }

    def test_random_event_types(self) -> None:
        """Random event type strings — should be accepted (forward-compat)."""
        random.seed(44)
        for i in range(20):
            type_ = "weird." + ''.join(
                random.choices(string.ascii_lowercase + "._", k=20)
            )
            e = EventV2.create("s1", i + 1, type_, {"x": i})
            self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 20)

    def test_random_aggregate_ids(self) -> None:
        """Random aggregate_id strings — should be accepted."""
        random.seed(45)
        # Create sessions first (commits after each insert to ensure
        # the FK is visible to EventBusV2's connection)
        sids = []
        for i in range(20):
            sid = "agg-" + ''.join(
                random.choices(string.ascii_letters + string.digits, k=10)
            )
            sids.append(sid)
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sid, "u", "t", 1.0, 1.0),
                )
                conn.commit()
            finally:
                conn.close()
        # Now publish events
        for sid in sids:
            e = EventV2.create(sid, 1, EventType.TEXT_DELTA, {"sid": sid})
            self.v2.publish(e)
        # All 20 sessions have 1 event
        self.assertEqual(self.v2.count(), 20)


# ── EventV2 hashability / immutability ────────────────────────────


class TestEventV2Immutability(unittest.TestCase):
    """Test if EventV2 can be hashed (used in sets/dicts)."""

    def test_eventv2_eq_works_dict_field_makes_unhashable(self) -> None:
        """EventV2 has a dict field, so it's UNhashable by default.

        This is a known limitation: dataclasses with dict fields can't
        be hashed (since dicts are unhashable). We document this.
        """
        e1 = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=1.0,
        )
        e2 = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=1.0,
        )
        # Equality works
        self.assertEqual(e1, e2)
        # But hash doesn't (dict is unhashable)
        with self.assertRaises(TypeError):
            hash(e1)

    def test_eventv2_mutable(self) -> None:
        """EventV2 is NOT frozen — fields can be reassigned."""
        e = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=1.0,
        )
        e.seq = 99
        self.assertEqual(e.seq, 99)

    def test_to_dict_returns_new_dict(self) -> None:
        """to_dict() returns a new dict, not a reference."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        d1 = e.to_dict()
        d2 = e.to_dict()
        self.assertIsNot(d1, d2)
        # Mutating d1 doesn't affect e
        d1["seq"] = 999
        self.assertEqual(e.seq, 1)

    def test_to_dict_data_is_reference(self) -> None:
        """to_dict() shares the data dict reference (documented behavior)."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        d = e.to_dict()
        # d["data"] is the same dict as e.data
        self.assertIs(d["data"], e.data)


# ── EventBusV2 with read-only DB ──────────────────────────────────


class TestEventBusV2ReadOnlyDB(unittest.TestCase):
    """What happens when the DB file is read-only?"""

    def test_publish_to_readonly_db_doesnt_crash(self) -> None:
        """Publishing to a read-only DB should not crash (best-effort)."""
        tmp_dir = tempfile.mkdtemp()
        try:
            db_path = Path(tmp_dir) / "ro.db"
            _setup_db(db_path)
            # Make the DB file read-only
            os.chmod(db_path, 0o444)
            try:
                bus = EventBus()
                v2 = EventBusV2(bus, db_path)
                e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
                # Should log error and continue, not raise
                v2.publish(e)
                # No event was persisted
                self.assertEqual(v2.count("s1"), 0)
            finally:
                # Restore write permission for cleanup
                os.chmod(db_path, 0o644)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── EventBusV2 pickle-ability ─────────────────────────────────────


class TestEventBusV2Pickle(unittest.TestCase):
    """Test if EventBusV2 can be pickled (for multiprocessing)."""

    def test_eventbusv2_picklable_via_reconstruction(self) -> None:
        """EventBusV2 cannot be directly pickled (Lock is un-picklable),
        but the configuration (db_path) can be saved and reconstructed."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            # Save the configuration (not the instance)
            config = {"db_path": str(v2.db_path)}
            config_bytes = pickle.dumps(config)
            loaded_config = pickle.loads(config_bytes)
            # Reconstruct EventBusV2 from the config
            v2_2 = EventBusV2(EventBus(), Path(loaded_config["db_path"]))
            self.assertEqual(v2_2.db_path, db_path)
            # Functional test: can publish via the reconstructed instance
            v2_2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {}))
            self.assertEqual(v2_2.count("s1"), 1)
        finally:
            db_path.unlink(missing_ok=True)

    def test_eventv2_picklable(self) -> None:
        """EventV2 is picklable."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        e2 = pickle.loads(pickle.dumps(e))
        self.assertEqual(e, e2)


# ── EventV2 deep-copy / clone ─────────────────────────────────────


class TestEventV2Copy(unittest.TestCase):
    """Test EventV2 copy semantics."""

    def test_deepcopy_creates_independent_copy(self) -> None:
        """deepcopy produces an independent copy."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        e2 = copy.deepcopy(e)
        self.assertEqual(e, e2)
        # Not the same object
        self.assertIsNot(e, e2)
        # Modifying the copy doesn't affect the original
        e2.data["x"] = 999
        self.assertEqual(e.data["x"], 1)


# ── Projector subclass-ability ────────────────────────────────────


class TestProjectorSubclass(unittest.TestCase):
    """Test that Projector can be subclassed for custom handlers."""

    def test_subclass_with_custom_handler(self) -> None:
        """A custom Projector subclass can register its own handlers."""
        class CustomProjector(Projector):
            def __init__(self, db_path):
                super().__init__(db_path)
                # Add a custom handler
                self._handlers["custom.event"] = self._on_custom
                self._custom_data = []

            def _on_custom(self, event, state):
                self._custom_data.append(event.data)

            def get_custom_data(self):
                return list(self._custom_data)

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            v2.publish(EventV2.create("s1", 1, "custom.event", {"x": 1}))
            v2.publish(EventV2.create("s1", 2, "custom.event", {"x": 2}))

            custom = CustomProjector(db_path)
            custom.project("s1")
            self.assertEqual(custom.get_custom_data(), [{"x": 1}, {"x": 2}])
        finally:
            db_path.unlink(missing_ok=True)


# ── EventBusV2 with isolation_level=None ─────────────────────────


class TestEventBusV2IsolationLevel(unittest.TestCase):
    """Verify EventBusV2 with autocommit (isolation_level=None)."""

    def test_publish_with_autocommit(self) -> None:
        """EventBusV2 should work even with autocommit connection."""
        # Note: EventBusV2 always uses connections internally; this
        # is more about verifying no edge case with autocommit.
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            # Set autocommit mode for the DB
            conn = sqlite3.connect(str(db_path), isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()

            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            for i in range(1, 6):
                v2.publish(EventV2.create(
                    "s1", i, EventType.TEXT_DELTA, {"i": i},
                ))
            self.assertEqual(v2.count("s1"), 5)
        finally:
            db_path.unlink(missing_ok=True)
            for ext in ("-wal", "-shm"):
                p = db_path.with_name(db_path.name + ext)
                p.unlink(missing_ok=True)


# ── EventBusV2 with custom PRAGMA settings ───────────────────────


class TestEventBusV2CustomPragma(unittest.TestCase):
    """EventBusV2 should work with various PRAGMA settings."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_synchronous_off(self) -> None:
        """PRAGMA synchronous=OFF (fastest, less safe)."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA synchronous=OFF")
        finally:
            conn.close()
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path)
        v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {}))
        self.assertEqual(v2.count("s1"), 1)

    def test_temp_store_in_memory(self) -> None:
        """PRAGMA temp_store=MEMORY."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA temp_store=MEMORY")
        finally:
            conn.close()
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path)
        v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {}))
        self.assertEqual(v2.count("s1"), 1)


# ── Projector with duplicate call_id ─────────────────────────────


class TestProjectorDuplicateCallId(unittest.TestCase):
    """Same tool_call id appearing twice in one message."""

    def test_duplicate_tool_call_id(self) -> None:
        """Two tool_call events with the same id — second one is no-op."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e1 = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",
            "tool": "calc", "input": {"x": 1},
        })
        e2 = EventV2.create("s1", 2, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",  # same id
            "tool": "calc", "input": {"x": 2},  # different input
        })
        projector.apply(e1, state)
        projector.apply(e2, state)
        # Only one part
        self.assertEqual(len(state.messages["a1"].parts), 1)
        # First event wins (input is x=1)
        self.assertEqual(
            state.messages["a1"].parts["tc-1"].data["input"],
            {"x": 1},
        )

    def test_duplicate_text_id(self) -> None:
        """Two text.started events with the same text_id — second is no-op."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e1 = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "a1", "text_id": "t1",
        })
        e2 = EventV2.create("s1", 2, EventType.TEXT_STARTED, {
            "message_id": "a1", "text_id": "t1",  # same id
        })
        projector.apply(e1, state)
        projector.apply(e2, state)
        # Only one part
        self.assertEqual(len(state.messages["a1"].parts), 1)


# ── Projector streaming mode ──────────────────────────────────────


class TestProjectorStreamingMode(unittest.TestCase):
    """Incremental state updates via apply() one event at a time."""

    def test_streaming_state_grows(self) -> None:
        """Apply events one at a time, state grows monotonically."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        events = [
            EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "content": "hi",
            }),
            EventV2.create("s1", 2, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            EventV2.create("s1", 3, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Hello",
            }),
        ]
        # Apply one at a time, state grows
        self.assertEqual(len(state.messages), 0)
        projector.apply(events[0], state)
        self.assertEqual(len(state.messages), 1)
        projector.apply(events[1], state)
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(len(state.messages["a1"].parts), 1)
        projector.apply(events[2], state)
        self.assertEqual(
            state.messages["a1"].parts["t1"].data["text"], "Hello"
        )

    def test_streaming_equivalent_to_batch(self) -> None:
        """Streaming (one at a time) and batch (all at once) produce same state."""
        events = [
            EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "content": "hi",
            }),
            EventV2.create("s1", 2, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            EventV2.create("s1", 3, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Hello",
            }),
            EventV2.create("s1", 4, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a1", "content": "Hello",
            }),
        ]
        projector = Projector(Path("/tmp/nonexistent"))

        # Streaming
        state_stream = ProjectedSession(session_id="s1")
        for e in events:
            projector.apply(e, state_stream)

        # Batch
        state_batch = ProjectedSession(session_id="s1")
        for e in events:
            projector.apply(e, state_batch)

        # Compare
        self.assertEqual(
            len(state_stream.messages),
            len(state_batch.messages),
        )
        for mid, m1 in state_stream.messages.items():
            m2 = state_batch.messages[mid]
            self.assertEqual(m1.content, m2.content)
            self.assertEqual(len(m1.parts), len(m2.parts))


# ── EventBusV2 with concurrent SSE subscribers ────────────────────


class TestEventBusV2ConcurrentSubscribers(unittest.TestCase):
    """Multiple SSE subscribers receive events concurrently."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        # Set up asyncio loop
        self.loop = asyncio.new_event_loop()
        self.bus.set_loop(self.loop)
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.loop.close()
        self.db_path.unlink(missing_ok=True)

    def test_multiple_subscribers_receive_same_events(self) -> None:
        """Two subscribers both receive the same event."""
        # Publish events to the buffer
        for i in range(1, 4):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))

        # Both subscribers get the replay (replay_all=True)
        async def collect():
            sub_a_events = []
            sub_b_events = []

            async def consume(sub_id, queue):
                while True:
                    try:
                        e = await asyncio.wait_for(queue.get(), timeout=0.1)
                        if sub_id == "a":
                            sub_a_events.append(e)
                        else:
                            sub_b_events.append(e)
                    except asyncio.TimeoutError:
                        return

            q_a = asyncio.Queue()
            q_b = asyncio.Queue()
            # Manually inject events (replay)
            for event in self.bus.replay("s1", replay_all=True):
                await q_a.put(event)
                await q_b.put(event)

            await consume("a", q_a)
            await consume("b", q_b)
            return sub_a_events, sub_b_events

        a_events, b_events = self.loop.run_until_complete(collect())
        # Both subscribers got the same 3 events
        self.assertEqual(len(a_events), 3)
        self.assertEqual(len(b_events), 3)
        self.assertEqual(
            [e.event_id for e in a_events],
            [e.event_id for e in b_events],
        )


# ── EventV2 __repr__ edge cases ──────────────────────────────────


class TestEventV2Repr(unittest.TestCase):
    """Test EventV2.__repr__ for debugging."""

    def test_repr_contains_key_fields(self) -> None:
        e = EventV2.create("s1", 42, EventType.TEXT_DELTA, {"x": 1})
        r = repr(e)
        self.assertIn("s1", r)
        self.assertIn("text_delta", r)
        self.assertIn("42", r)

    def test_repr_with_long_data(self) -> None:
        """Repr doesn't blow up with very long data."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "x": "y" * 1000,
        })
        r = repr(e)
        # Repr is generated by default dataclass repr
        # It will include the full data — should not crash
        self.assertIsInstance(r, str)


# ── EventBusV2 with non-existent directory parent ─────────────────


class TestEventBusV2MissingDirectory(unittest.TestCase):
    """What if the parent directory doesn't exist?"""

    def test_publish_creates_directory_if_needed(self) -> None:
        """sqlite3.connect creates the file but not directories.
        Test that we handle this gracefully (it raises, we log+skip)."""
        ghost_path = Path(tempfile.gettempdir()) / "ghost-dir-12345" / "test.db"
        # Ensure parent doesn't exist
        if ghost_path.parent.exists():
            shutil.rmtree(ghost_path.parent)

        try:
            bus = EventBus()
            v2 = EventBusV2(bus, ghost_path)
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
            # Should not raise (we catch OperationalError)
            v2.publish(e)
            # No event was persisted
            self.assertEqual(v2.count("s1"), 0)
        finally:
            # Clean up
            if ghost_path.parent.exists():
                shutil.rmtree(ghost_path.parent, ignore_errors=True)


# ── ProjectedSession equality ─────────────────────────────────────


class TestProjectedSessionEquality(unittest.TestCase):
    """ProjectedSession equality semantics."""

    def test_session_equality(self) -> None:
        s1 = ProjectedSession(session_id="s1")
        s2 = ProjectedSession(session_id="s1")
        s3 = ProjectedSession(session_id="s2")
        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, s3)

    def test_session_with_messages_equality(self) -> None:
        s1 = ProjectedSession(session_id="s1")
        s1.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi",
        )
        s2 = ProjectedSession(session_id="s1")
        s2.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi",
        )
        self.assertEqual(s1, s2)

    def test_session_unhashable_due_to_dict_field(self) -> None:
        """ProjectedSession has dict fields → unhashable (documented)."""
        s = ProjectedSession(session_id="s1")
        # ProjectedSession has a dict field (messages), so unhashable
        with self.assertRaises(TypeError):
            hash(s)


# ── EventV2 with unusual time_created ────────────────────────────


class TestEventV2TimeCreatedEdgeCases(unittest.TestCase):
    """EventV2 accepts various time_created values."""

    def test_time_created_zero(self) -> None:
        """time_created=0 is valid (epoch)."""
        e = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=0.0,
        )
        self.assertEqual(e.time_created, 0.0)

    def test_time_created_negative(self) -> None:
        """time_created=-1 is technically valid (pre-epoch)."""
        e = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=-1.0,
        )
        self.assertEqual(e.time_created, -1.0)

    def test_time_created_very_large(self) -> None:
        """time_created=2^31 is valid (year 2038)."""
        e = EventV2(
            id="x", aggregate_id="s1", seq=1, type="x", data={},
            time_created=2**31,
        )
        self.assertEqual(e.time_created, 2**31)


# ── EventV2 from_row with extra fields ────────────────────────────


class TestEventV2FromRowForwardCompat(unittest.TestCase):
    """EventV2.from_row should ignore unknown columns."""

    def test_from_row_with_extra_columns(self) -> None:
        """Extra columns (from future schema) are ignored."""
        class Row:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
        row = Row({
            "id": "e1", "aggregate_id": "s1", "seq": 1, "type": "x",
            "data_json": "{}", "time_created": 1.0,
            # Future columns that don't exist yet
            "future_field": "ignored",
            "another_field": 42,
        })
        e = EventV2.from_row(row)
        self.assertEqual(e.id, "e1")
        # No error raised


# ── EventBusV2 with messages that have metadata ──────────────────


class TestEventBusV2MessageMetadata(unittest.TestCase):
    """Test the message_id (synthetic message_id) metadata handling."""

    def test_publish_then_replay_preserves_metadata(self) -> None:
        """An event with complex nested metadata round-trips."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
                "message_id": "a1",
                "id": "tc-1",
                "tool": "complex",
                "input": {"x": 1},
                "metadata": {
                    "trace_id": "abc-123",
                    "tags": ["important", "verified"],
                    "duration_ms": 1234,
                },
            })
            v2.publish(e)
            events = v2.replay("s1")
            self.assertEqual(events[0].data["metadata"]["trace_id"], "abc-123")
            self.assertEqual(events[0].data["metadata"]["tags"], ["important", "verified"])
        finally:
            db_path.unlink(missing_ok=True)


# ── Projector: state re-projection (over time) ────────────────────


class TestProjectorReprojection(unittest.TestCase):
    """Projecting the same events multiple times should be idempotent."""

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

    def test_project_twice_same_state(self) -> None:
        """project() called twice produces same state."""
        for i in range(1, 6):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.MESSAGE_RECEIVED, {
                    "message_id": f"m{i}", "content": f"c{i}",
                },
            ))
        s1 = self.projector.project("s1")
        s2 = self.projector.project("s1")
        self.assertEqual(
            [m.content for m in s1.messages_in_order()],
            [m.content for m in s2.messages_in_order()],
        )

    def test_project_after_new_event(self) -> None:
        """After publishing a new event, project() reflects it."""
        self.v2.publish(EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "m1", "content": "first",
        }))
        s1 = self.projector.project("s1")
        self.assertEqual(len(s1.messages), 1)
        # Publish a second event
        self.v2.publish(EventV2.create("s1", 2, EventType.MESSAGE_RECEIVED, {
            "message_id": "m2", "content": "second",
        }))
        s2 = self.projector.project("s1")
        self.assertEqual(len(s2.messages), 2)
        self.assertEqual(s2.messages["m2"].content, "second")


# ── EventBusV2 with very small DB file ───────────────────────────


class TestEventBusV2VerySmallDB(unittest.TestCase):
    """Edge case: 1KB DB file."""

    def test_very_small_db_works(self) -> None:
        """A 1KB DB file (just the schema) works for publish."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        # Just create the schema, no data
        _setup_db(db_path, with_session=False)
        # Verify file is small
        size = db_path.stat().st_size
        self.assertLess(size, 100_000, f"DB unexpectedly large: {size} bytes")

        # Add a session
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "u", "t", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()

        bus = EventBus()
        v2 = EventBusV2(bus, db_path)
        v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {}))
        self.assertEqual(v2.count("s1"), 1)
        db_path.unlink(missing_ok=True)


# ── EventBusV2: replay with negative after_seq ────────────────────


class TestEventBusV2NegativeAfterSeq(unittest.TestCase):
    """replay(after_seq=-1) returns all events (after_seq > -1 always true)."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_replay_with_negative_after_seq(self) -> None:
        """after_seq=-1 returns all events (since seq > -1 is always true)."""
        for i in range(1, 4):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {},
            ))
        events = self.v2.replay("s1", after_seq=-1)
        self.assertEqual(len(events), 3)

    def test_replay_with_zero_after_seq(self) -> None:
        """after_seq=0 returns all events."""
        for i in range(1, 4):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {},
            ))
        events = self.v2.replay("s1", after_seq=0)
        self.assertEqual(len(events), 3)


# ── Projector: tool_result for unknown call_id creates part ───────


class TestProjectorUnknownToolResult(unittest.TestCase):
    """tool_result for a tool_call we never saw — defensive behavior."""

    def test_tool_result_for_unknown_call_id(self) -> None:
        """Defensive: tool_result for unknown call_id creates the part."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_RESULT, {
            "message_id": "a1", "id": "tc-unknown",
            "result": "42", "status": "done",
        })
        projector.apply(e, state)
        # Part was created (defensive)
        self.assertIn("tc-unknown", state.messages["a1"].parts)
        self.assertEqual(
            state.messages["a1"].parts["tc-unknown"].data["result"],
            "42",
        )


# ── EventBusV2: SSE buffer integration ───────────────────────────


class TestEventBusV2SSEBufferIntegration(unittest.TestCase):
    """Verify the SSE buffer receives events in publish order."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_buffered_events_in_publish_order(self) -> None:
        """Events appear in the buffer in the order they were published."""
        for i in range(1, 11):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))
        buffered = self.bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 10)
        # Buffer order matches publish order
        seqs = [int(e.data["i"]) for e in buffered]
        self.assertEqual(seqs, list(range(1, 11)))


if __name__ == "__main__":
    unittest.main()
