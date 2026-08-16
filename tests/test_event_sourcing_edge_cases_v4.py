"""Round 4 edge case tests for Phase 3 B1.

Continuing to add tests even though marginal returns are
diminishing. These cover:

1. Property-based tests using hypothesis (auto-generated events)
2. Concurrent publish + replay (read-while-write)
3. Process restart simulation (close/reopen DB)
4. EventBusV2 isolation between EventBus instances
5. Stress test with 10k events
6. Projector thread-safety on shared state
7. EventBusV2 with maximum data coverage
8. Schema evolution: extra columns in event_log
9. EventBusV2 with multiple aggregates interleaved
10. EventBusV2 forward-compat for very long event types
"""
from __future__ import annotations

import gc
import json
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


# ── Property-based tests (hypothesis) ────────────────────────────


try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    from hypothesis.strategies import composite

    @composite
    def event_v2_strategy(draw, max_seq: int = 100) -> EventV2:
        """Generate a random valid EventV2."""
        seq = draw(st.integers(min_value=1, max_value=max_seq))
        type_ = draw(st.sampled_from([
            EventType.TEXT_DELTA,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
            EventType.MESSAGE_RECEIVED,
            EventType.ASSISTANT_MESSAGE,
            EventType.AGENT_DONE,
            EventType.ITER_START,
        ]))
        data = draw(st.dictionaries(
            keys=st.sampled_from(["message_id", "text", "id", "tool", "result"]),
            values=st.one_of(
                st.text(min_size=0, max_size=50),
                st.integers(min_value=0, max_value=1000),
                st.booleans(),
            ),
            min_size=0,
            max_size=3,
        ))
        return EventV2.create("s1", seq, type_, data)

    @composite
    def unique_event_stream(draw, n: int = 10) -> list[EventV2]:
        """Generate n events with unique seq numbers."""
        seqs = draw(st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=n, max_size=n, unique=True,
        ))
        seqs.sort()
        events = []
        for i, seq in enumerate(seqs):
            type_ = draw(st.sampled_from([
                EventType.TEXT_DELTA,
                EventType.MESSAGE_RECEIVED,
                EventType.ASSISTANT_MESSAGE,
            ]))
            events.append(EventV2.create("s1", seq, type_, {
                "message_id": f"m{i}",
                "content": f"content-{i}",
            }))
        return events

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False


@unittest.skipUnless(HYPOTHESIS_AVAILABLE, "hypothesis not installed")
class TestEventV2PropertyBased(unittest.TestCase):
    """Property-based tests for EventV2 round-trip."""

    @given(st.integers(min_value=1, max_value=1000000))
    @settings(max_examples=50)
    def test_seq_preserved_through_round_trip(self, seq: int) -> None:
        """Any positive integer seq is preserved through JSON."""
        e = EventV2.create("s1", seq, EventType.TEXT_DELTA, {})
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.seq, seq)

    @given(st.text(min_size=1, max_size=100))
    @settings(max_examples=50)
    def test_aggregate_id_preserved(self, agg_id: str) -> None:
        """Any non-empty aggregate_id is preserved."""
        # Filter out non-printable chars that might break serialization
        agg_id = "".join(c for c in agg_id if c.isprintable())
        if not agg_id:
            return
        e = EventV2.create(agg_id, 1, EventType.TEXT_DELTA, {})
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.aggregate_id, agg_id)

    @given(st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.one_of(
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.booleans(),
            st.text(max_size=50),
        ),
        min_size=0,
        max_size=5,
    ))
    @settings(max_examples=50)
    def test_data_dict_preserved(self, data: dict) -> None:
        """Any JSON-serializable dict is preserved through round-trip."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, data)
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data, data)

    def test_unique_seq_preserved(self) -> None:
        """Generating a stream of events with unique seq works."""
        for _ in range(10):
            events = unique_event_stream().example()
            self.assertEqual(len(events), 10)
            seqs = [e.seq for e in events]
            self.assertEqual(len(set(seqs)), 10, "seqs must be unique")


@unittest.skipUnless(HYPOTHESIS_AVAILABLE, "hypothesis not installed")
class TestEventBusV2PropertyBased(unittest.TestCase):
    """Property-based tests for EventBusV2.

    Note: hypothesis re-runs the test function multiple times in the
    same process, and the example database persists failing examples
    across runs. setUp/tearDown is called once per @given test, not
    per example, which means we can't use a fresh DB per example
    without major restructuring.

    Instead, we test simpler invariants that don't depend on
    per-example state isolation.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    @given(
        data=st.dictionaries(
            keys=st.text(min_size=1, max_size=20),
            values=st.one_of(
                st.integers(),
                st.text(max_size=50),
                st.booleans(),
            ),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=30, deadline=2000)
    def test_arbitrary_data_roundtrips(self, data: dict) -> None:
        """Any JSON-serializable data round-trips through event_log.

        Each example uses a unique session_id (uuid) so no collisions.
        """
        import uuid
        session_id = f"hyp-{uuid.uuid4()}"
        # Create the session
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, "u", "t", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()

        e = EventV2.create(session_id, 1, EventType.TEXT_DELTA, data)
        self.v2.publish(e)
        replayed = self.v2.replay(session_id)
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].data, data)

    def test_arbitrary_seq_preserved(self) -> None:
        """Single example: any positive seq is preserved."""
        # Just use a regular test, not @given
        e = EventV2.create("s1", 12345, EventType.TEXT_DELTA, {})
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.seq, 12345)


# ── Concurrent publish + replay (read-while-write) ───────────────


class TestEventBusV2ConcurrentReadWrite(unittest.TestCase):
    """Verify read-while-write consistency."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_while_replay(self) -> None:
        """One thread publishes, another replays concurrently.

        The invariants we verify:
        1. No crashes from concurrent access
        2. count is consistent with last_seq at the end
        3. Replays never return duplicate seqs within a single call
        """
        stop = threading.Event()
        errors: list[Exception] = []

        def publisher() -> None:
            i = 1
            while not stop.is_set():
                try:
                    self.v2.publish(EventV2.create(
                        "s1", i, EventType.TEXT_DELTA, {"i": i},
                    ))
                    i += 1
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)

        def reader() -> None:
            for _ in range(20):
                try:
                    events = self.v2.replay("s1")
                    # Within a single replay, seqs must be unique
                    seqs = [e.seq for e in events]
                    if len(set(seqs)) != len(seqs):
                        errors.append(
                            Exception(f"Replay returned duplicate seqs: {seqs}")
                        )
                    # And they must be in order
                    if seqs != sorted(seqs):
                        errors.append(
                            Exception(f"Replay returned out-of-order: {seqs}")
                        )
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.005)
            stop.set()

        pub_t = threading.Thread(target=publisher)
        read_t = threading.Thread(target=reader)
        pub_t.start()
        read_t.start()
        pub_t.join()
        read_t.join()

        # No errors should have occurred
        self.assertEqual(errors, [], f"Concurrent errors: {errors[:3]}")
        # After stop, the count is consistent with last_seq
        count = self.v2.count("s1")
        last = self.v2.last_seq("s1")
        # count should be == last (every seq 1..last was published)
        self.assertEqual(count, last, f"count={count} last={last}")


# ── Process restart simulation ────────────────────────────────────


class TestEventBusV2ProcessRestart(unittest.TestCase):
    """Simulate a process restart: persist events, close, reopen."""

    def test_persist_close_reopen_replay(self) -> None:
        """Events persist across process restarts (close + reopen DB)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)

        # First "process": publish events
        bus1 = EventBus()
        v2_1 = EventBusV2(bus1, db_path)
        for i in range(1, 11):
            v2_1.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))
        # bus1 buffer has events; v2_1 is still alive
        # Simulate process exit by dropping references
        del bus1
        del v2_1
        gc.collect()

        # Second "process": new EventBus + EventBusV2 against same DB
        bus2 = EventBus()
        v2_2 = EventBusV2(bus2, db_path)
        # Replay recovers the events
        events = v2_2.replay("s1")
        self.assertEqual(len(events), 10)
        self.assertEqual([e.seq for e in events], list(range(1, 11)))
        # last_seq is preserved
        self.assertEqual(v2_2.last_seq("s1"), 10)
        # count is preserved
        self.assertEqual(v2_2.count("s1"), 10)

        db_path
        db_path.unlink(missing_ok=True)


# ── EventBusV2 isolation between EventBus instances ─────────────


class TestEventBusV2EventBusIsolation(unittest.TestCase):
    """Two EventBusV2 instances with different EventBus instances
    should not interfere with each other's SSE delivery."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus_a = EventBus()
        self.bus_b = EventBus()
        self.v2_a = EventBusV2(self.bus_a, self.db_path)
        self.v2_b = EventBusV2(self.bus_b, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_two_v2_instances_same_db_different_eventbus(self) -> None:
        """Both EventBusV2 share the same DB but route SSE to different
        EventBus instances. They should not interfere."""
        self.v2_a.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {"src": "a"}))
        self.v2_b.publish(EventV2.create("s1", 2, EventType.TEXT_DELTA, {"src": "b"}))
        # Both events in DB
        self.assertEqual(self.v2_a.count("s1"), 2)
        self.assertEqual(self.v2_b.count("s1"), 2)
        # bus_a has only event 1
        events_a = self.bus_a.replay("s1", replay_all=True)
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0].data, {"src": "a"})
        # bus_b has only event 2
        events_b = self.bus_b.replay("s1", replay_all=True)
        self.assertEqual(len(events_b), 1)
        self.assertEqual(events_b[0].data, {"src": "b"})


# ── Stress test: 10k events ───────────────────────────────────────


class TestEventBusV2Stress10k(unittest.TestCase):
    """Verify the system can handle 10k events."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_10k_events(self) -> None:
        """10k events should publish and replay correctly."""
        start = time.time()
        N = 10_000
        for i in range(1, N + 1):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i, "text": f"x{i}"},
            ))
        elapsed = time.time() - start
        print(f"\n[10k publish] {elapsed:.2f}s, {N/elapsed:.0f} events/s")
        self.assertEqual(self.v2.count("s1"), N)
        self.assertEqual(self.v2.last_seq("s1"), N)

    def test_replay_10k_events(self) -> None:
        """Replaying 10k events is fast."""
        N = 10_000
        # Setup: publish
        for i in range(1, N + 1):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))
        start = time.time()
        events = self.v2.replay("s1")
        elapsed = time.time() - start
        print(f"\n[10k replay] {elapsed:.2f}s, {len(events)} events")
        self.assertEqual(len(events), N)
        self.assertEqual(events[0].seq, 1)
        self.assertEqual(events[-1].seq, N)


# ── Projector thread safety ──────────────────────────────────────


class TestProjectorThreadSafety(unittest.TestCase):
    """Projector is pure (stateless), but state mutation via apply()
    needs to be thread-safe if multiple threads share a state."""

    def test_concurrent_apply_to_shared_state(self) -> None:
        """Multiple threads apply events to the same state object.

        The state dict operations aren't atomic in Python's GIL model,
        but Python's GIL provides dict-level safety. We just verify
        no crashes and final state is consistent.
        """
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        N_THREADS = 4
        N_EVENTS = 100
        errors: list[Exception] = []

        def apply_batch(tid: int) -> None:
            try:
                for i in range(N_EVENTS):
                    e = EventV2.create(
                        "s1", tid * N_EVENTS + i + 1,
                        EventType.MESSAGE_RECEIVED,
                        {"message_id": f"m-{tid}-{i}", "content": f"hi-{tid}-{i}"},
                    )
                    projector.apply(e, state)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=apply_batch, args=(t,))
                   for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent apply errors: {errors}")
        # All N_THREADS * N_EVENTS messages should be in state
        self.assertEqual(len(state.messages), N_THREADS * N_EVENTS)


# ── EventBusV2 with maximum data coverage ────────────────────────


class TestEventBusV2MaximumData(unittest.TestCase):
    """Test EventBusV2 with maximum data (all possible fields)."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_event_with_maximum_data(self) -> None:
        """An event with all possible fields populated round-trips."""
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "m1",
            "id": "tc-1",
            "tool": "complex_tool",
            "input": {"x": 1, "y": [1, 2, 3]},
            "function": {
                "name": "complex_tool",
                "arguments": '{"x": 1, "y": [1, 2, 3]}',
            },
            "result": "ok",
            "status": "done",
            "metadata": {
                "duration_ms": 1234,
                "tags": ["important", "traced"],
            },
            "custom_field": "anything",
            "nested": {"deep": {"deeper": "value"}},
        })
        self.v2.publish(e)
        # Replay and verify
        events = self.v2.replay("s1")
        self.assertEqual(len(events), 1)
        e2 = events[0]
        self.assertEqual(e2.data["input"], {"x": 1, "y": [1, 2, 3]})
        self.assertEqual(e2.data["metadata"]["duration_ms"], 1234)
        self.assertEqual(e2.data["nested"]["deep"]["deeper"], "value")


# ── Schema evolution: extra columns in event_log ─────────────────


class TestEventBusV2SchemaEvolution(unittest.TestCase):
    """What if event_log has an extra column (e.g., from a future
    migration)? EventBusV2 should still work because we use named
    columns in INSERT, not positional."""

    def test_eventbusv2_with_extra_column(self) -> None:
        """Add an extra column to event_log and verify publish still works."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                    created_at REAL, updated_at REAL
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
                    future_field TEXT DEFAULT NULL,
                    FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    UNIQUE (aggregate_id, seq)
                )
            """)
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
        # Publish should still work (we use named columns)
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        v2.publish(e)
        # Replay
        events = v2.replay("s1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, {"x": 1})

        db_path.unlink(missing_ok=True)


# ── EventBusV2 with multiple aggregates interleaved ─────────────


class TestEventBusV2MultipleAggregates(unittest.TestCase):
    """Test with multiple session IDs being written to in parallel."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        # Create 5 sessions
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                    created_at REAL, updated_at REAL
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
            for i in range(5):
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"agg-{i}", "u", "t", 1.0, 1.0),
                )
            conn.commit()
        finally:
            conn.close()
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_five_aggregates_interleaved(self) -> None:
        """Write events to 5 different sessions, interleaved seq numbers."""
        # Round-robin: 1→agg-0, 2→agg-1, 3→agg-2, 4→agg-3, 5→agg-4, 6→agg-0, ...
        seq = 0
        for round_num in range(20):
            for agg_idx in range(5):
                seq += 1
                self.v2.publish(EventV2.create(
                    f"agg-{agg_idx}", seq, EventType.TEXT_DELTA,
                    {"round": round_num, "agg": agg_idx},
                ))

        # Each aggregate has 20 events
        for agg_idx in range(5):
            count = self.v2.count(f"agg-{agg_idx}")
            self.assertEqual(count, 20, f"agg-{agg_idx} has {count} events")

        # Each aggregate's last_seq is the highest seq written to it
        # In our round-robin, the highest seqs for each aggregate are:
        # agg-0: 100 (last round, idx 0) — no wait, seq goes 1..100
        # Round 1: 1,2,3,4,5
        # Round 20: 96,97,98,99,100
        # agg-0 gets seqs 1, 6, 11, ..., 96 → last_seq = 96
        for agg_idx in range(5):
            last = self.v2.last_seq(f"agg-{agg_idx}")
            # agg-idx got seqs (1+idx), (6+idx), ..., (96+idx)
            expected = 96 + agg_idx
            self.assertEqual(last, expected, f"agg-{agg_idx} last={last}, expected={expected}")


# ── EventBusV2 forward-compat for very long event types ──────────


class TestEventBusV2LongEventTypes(unittest.TestCase):
    """Event types can be very long in theory."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_very_long_event_type(self) -> None:
        """A 200-character event type round-trips."""
        long_type = "namespace." + ".".join(["x" * 20 for _ in range(8)])
        e = EventV2.create("s1", 1, long_type, {"x": 1})
        self.v2.publish(e)
        events = self.v2.replay("s1")
        self.assertEqual(events[0].type, long_type)

    def test_event_type_with_many_dots(self) -> None:
        """Event types can have many nested namespaces."""
        type_ = ".".join(["a", "b", "c", "d", "e", "f", "g", "h"])
        e = EventV2.create("s1", 1, type_, {})
        self.v2.publish(e)
        events = self.v2.replay("s1")
        self.assertEqual(events[0].type, type_)


# ── Projector replay determinism ─────────────────────────────────


class TestProjectorReplayDeterminism(unittest.TestCase):
    """Replaying the same events should always produce the same state."""

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

    def test_replay_is_deterministic(self) -> None:
        """Two consecutive project() calls produce identical state."""
        # Publish a varied event stream
        events_data = [
            (1, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "role": "user", "content": "hi",
            }),
            (2, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            (3, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Hello",
            }),
            (4, EventType.TOOL_CALL, {
                "message_id": "a1", "id": "tc-1",
                "tool": "calc", "input": {"x": 1},
            }),
            (5, EventType.TOOL_RESULT, {
                "message_id": "a1", "id": "tc-1",
                "result": "42", "status": "done",
            }),
            (6, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a1", "content": "Hello",
            }),
        ]
        for seq, et, data in events_data:
            self.v2.publish(EventV2.create("s1", seq, et, data))

        # First project
        state1 = self.projector.project("s1")
        # Second project
        state2 = self.projector.project("s1")

        # Compare the two states
        self.assertEqual(len(state1.messages), len(state2.messages))
        for mid, m1 in state1.messages.items():
            m2 = state2.messages.get(mid)
            self.assertIsNotNone(m2, f"m2 missing {mid}")
            self.assertEqual(m1.role, m2.role)
            self.assertEqual(m1.content, m2.content)
            self.assertEqual(len(m1.parts), len(m2.parts))
            for pid, p1 in m1.parts.items():
                p2 = m2.parts.get(pid)
                self.assertIsNotNone(p2, f"p2 missing {pid}")
                self.assertEqual(p1.type, p2.type)
                self.assertEqual(p1.data, p2.data)


# ── EventBusV2 with mixed event types in one stream ──────────────


class TestEventBusV2MixedEventTypes(unittest.TestCase):
    """A realistic stream with many event types interleaved."""

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

    def test_realistic_700dc7f7_like_stream(self) -> None:
        """Simulate a 700dc7f7-like stream with 30+ events."""
        events_data = [
            (1, EventType.SESSION_CREATED, {"session_id": "s1", "title": "t"}),
            (2, EventType.MESSAGE_RECEIVED, {
                "message_id": "u-1", "role": "user",
                "content": "Calculate factor values for 000001.SZ",
            }),
            (3, EventType.ATTEMPT_CREATED, {
                "attempt_id": "a-1", "prompt": "...",
            }),
            (4, EventType.ITER_START, {"iteration": 1, "max_iterations": 5}),
            (5, EventType.THINKING_START, {}),
            (6, EventType.THINKING_DELTA, {"delta": "I need to"}),
            (7, EventType.THINKING_DONE, {}),
            (8, EventType.TEXT_STARTED, {
                "message_id": "a-1", "text_id": "t-1",
            }),
            (9, EventType.TEXT_DELTA, {
                "message_id": "a-1", "text_id": "t-1", "text": "Let me check.",
            }),
            (10, EventType.TOOL_CALL, {
                "message_id": "a-1", "id": "tc-load",
                "tool": "import_data", "input": {"codes": ["000001.SZ"]},
            }),
            (11, EventType.TOOL_PROGRESS, {
                "message_id": "a-1", "id": "tc-load",
                "stage": "fetching", "current": 50, "total": 100,
            }),
            (12, EventType.TOOL_PROGRESS, {
                "message_id": "a-1", "id": "tc-load",
                "stage": "fetching", "current": 100, "total": 100,
            }),
            (13, EventType.TOOL_RESULT, {
                "message_id": "a-1", "id": "tc-load",
                "result": "100 rows imported", "status": "done",
            }),
            (14, EventType.TEXT_DELTA, {
                "message_id": "a-1", "text_id": "t-1", "text": " Got data.",
            }),
            (15, EventType.TEXT_ENDED, {
                "message_id": "a-1", "text_id": "t-1",
                "text": "Let me check. Got data.",
            }),
            (16, EventType.TOOL_CALL, {
                "message_id": "a-1", "id": "tc-calc",
                "tool": "compute_factor", "input": {"factor": "momentum"},
            }),
            (17, EventType.TOOL_RESULT, {
                "message_id": "a-1", "id": "tc-calc",
                "result": "factor=0.85", "status": "done",
            }),
            (18, EventType.LLM_USAGE, {
                "input_tokens": 1000, "output_tokens": 200,
            }),
            (19, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a-1",
                "content": "Let me check. Got data. Factor is 0.85.",
            }),
            (20, EventType.ITER_END, {"iteration": 1}),
            (21, EventType.AGENT_DONE, {
                "message_id": "a-1", "status": "success",
            }),
        ]
        for seq, et, data in events_data:
            self.v2.publish(EventV2.create("s1", seq, et, data))

        # All 21 events in event_log
        self.assertEqual(self.v2.count("s1"), 21)

        # Projector state: 2 messages (user + assistant)
        state = self.projector.project("s1")
        self.assertEqual(len(state.messages), 2)
        # User message
        self.assertIn("u-1", state.messages)
        self.assertEqual(state.messages["u-1"].content, "Calculate factor values for 000001.SZ")
        # Assistant: 1 text part + 2 tool_call parts = 3 parts
        a = state.messages["a-1"]
        self.assertEqual(len(a.parts), 3)
        self.assertIn("t-1", a.parts)
        self.assertIn("tc-load", a.parts)
        self.assertIn("tc-calc", a.parts)
        # Text part has accumulated text
        self.assertEqual(a.parts["t-1"].data["text"], "Let me check. Got data.")
        # Tool parts have results
        self.assertEqual(a.parts["tc-load"].data["result"], "100 rows imported")
        self.assertEqual(a.parts["tc-calc"].data["result"], "factor=0.85")
        # Tool progress preserved
        progress = a.parts["tc-load"].data["progress"]
        self.assertEqual(len(progress), 2)
        self.assertEqual(progress[0]["current"], 50)
        self.assertEqual(progress[1]["current"], 100)


# ── Projector: from_row with various data_json formats ───────────


class TestProjectorFromRowDataFormats(unittest.TestCase):
    """Test Projector with event_log rows that have data_json as
    either a string (from DB) or already-parsed dict (in-memory)."""

    def test_from_row_with_string_data_json(self) -> None:
        """data_json is a JSON string (normal case from DB)."""
        from strategy_research.core.events.event_v2 import EventV2
        # Build a row-like object
        class Row:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
        row = Row({
            "id": "e1", "aggregate_id": "s1", "seq": 1, "type": "text_delta",
            "data_json": json.dumps({"text": "hi"}),
            "time_created": 1.0,
        })
        e = EventV2.from_row(row)
        self.assertEqual(e.data, {"text": "hi"})

    def test_from_row_with_dict_data_json(self) -> None:
        """data_json is already a dict (in-memory case)."""
        from strategy_research.core.events.event_v2 import EventV2
        class Row:
            def __init__(self, d):
                self._d = d
            def __getitem__(self, k):
                return self._d[k]
        row = Row({
            "id": "e1", "aggregate_id": "s1", "seq": 1, "type": "text_delta",
            "data_json": {"text": "hi"},  # already parsed
            "time_created": 1.0,
        })
        e = EventV2.from_row(row)
        self.assertEqual(e.data, {"text": "hi"})


# ── EventBusV2: replay_all forward compat with EventBus ──────────


class TestEventBusV2EventBusReplayCompat(unittest.TestCase):
    """Verify EventBusV2 + EventBus interaction is forward-compatible."""

    def test_event_id_matches_across_sinks(self) -> None:
        """The event_id in the SSEEvent buffer must match the event_log id."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.tmp.name if hasattr(tmp, 'tmp') else tmp.name)
        _setup_db(db_path)
        bus = EventBus()
        v2 = EventBusV2(bus, db_path)
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"x": 1})
        v2.publish(e)
        # SSE event has same id as event_log
        buffered = bus.replay("s1", replay_all=True)
        self.assertEqual(len(buffered), 1)
        self.assertEqual(buffered[0].event_id, e.id)
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
