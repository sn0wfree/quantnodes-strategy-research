"""Round 3 edge case tests for Phase 3 B1.

More gaps covered in this file:
1. Projector handlers for media events (file_edit, table, chart, image)
2. Projector with dangling parts (text.started without text.ended)
3. Projector with dangling tool calls (tool.call without tool.result)
4. Projector with only thinking events (no message events)
5. Projector after_seq > last_seq (empty result)
6. EventBusV2 replay after_seq > last_seq
7. EventBusV2 with empty string in data
8. Projector with many parts in one message (stress test, 50+)
9. Interleaved message_ids (text part for m1, m2, back to m1)
10. EventBusV2 publish with very long payload
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.event_v2 import EventType, EventV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.projector import (
    ProjectedMessage,
    ProjectedPart,
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


# ── Projector: media event handlers ───────────────────────────────


class TestProjectorMediaEvents(unittest.TestCase):
    """file_edit, table, chart, image events are persisted as parts
    (B7-2 defense-in-depth): each lazy-creates its assistant message
    and records a typed part so a future backend emit is never
    silently dropped."""

    def setUp(self) -> None:
        self.state = ProjectedSession(session_id="s1")
        self.projector = Projector(Path("/tmp/nonexistent"))

    def test_file_edit_absorbed(self) -> None:
        e = EventV2.create("s1", 1, EventType.FILE_EDIT, {
            "message_id": "a1", "file_path": "/tmp/foo.py",
            "old_content": "a", "new_content": "b",
        })
        self.projector.apply(e, self.state)
        part = self.state.messages["a1"].parts["file_edit_1"]
        self.assertEqual(part.type, "file_edit")
        self.assertEqual(part.data["file_path"], "/tmp/foo.py")
        self.assertEqual(part.data["new_content"], "b")

    def test_table_absorbed(self) -> None:
        e = EventV2.create("s1", 1, EventType.TABLE, {
            "message_id": "a1", "headers": ["a", "b"],
            "rows": [["c", "d"]],
        })
        self.projector.apply(e, self.state)
        part = self.state.messages["a1"].parts["table_1"]
        self.assertEqual(part.type, "table")
        self.assertEqual(part.data["rows"], [["c", "d"]])

    def test_chart_absorbed(self) -> None:
        e = EventV2.create("s1", 1, EventType.CHART, {
            "message_id": "a1", "chart_type": "line", "data": [1, 2, 3],
        })
        self.projector.apply(e, self.state)
        part = self.state.messages["a1"].parts["chart_1"]
        self.assertEqual(part.type, "chart")
        self.assertEqual(part.data["data"], [1, 2, 3])

    def test_image_absorbed(self) -> None:
        e = EventV2.create("s1", 1, EventType.IMAGE, {
            "message_id": "a1", "url": "https://example.com/x.png",
        })
        self.projector.apply(e, self.state)
        part = self.state.messages["a1"].parts["image_1"]
        self.assertEqual(part.type, "image")
        self.assertEqual(part.data["url"], "https://example.com/x.png")


# ── Projector: dangling parts ──────────────────────────────────────


class TestProjectorDanglingParts(unittest.TestCase):
    """A part that never received its 'ended' event should still be valid."""

    def test_text_started_without_text_ended(self) -> None:
        """text.started but never text.ended — the part should still exist."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e1 = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "a1", "text_id": "t1",
        })
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {
            "message_id": "a1", "text_id": "t1", "text": "half",
        })
        projector.apply(e1, state)
        projector.apply(e2, state)
        # No text.ended — the part should still be there
        self.assertIn("t1", state.messages["a1"].parts)
        self.assertEqual(state.messages["a1"].parts["t1"].data["text"], "half")

    def test_tool_call_without_tool_result(self) -> None:
        """tool.call but never tool.result — part should exist with no result."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",
            "tool": "fetch", "input": {},
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["state"], "call")
        self.assertEqual(part.data["status"], "running")
        self.assertNotIn("result", part.data)


# ── Projector: only thinking events ────────────────────────────────


class TestProjectorOnlyThinkingEvents(unittest.TestCase):
    """Pure thinking stream: the message is lazy-created with one
    thinking part (collapsed by default, B7-2)."""

    def test_thinking_only_stream(self) -> None:
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        for et in (EventType.THINKING_START, EventType.THINKING_DELTA,
                   EventType.THINKING_DELTA, EventType.THINKING_DONE):
            e = EventV2.create("s1", 1, et, {"message_id": "a1", "delta": "..."})
            projector.apply(e, state)
        self.assertIn("a1", state.messages)
        parts = state.messages["a1"].parts
        self.assertEqual(len(parts), 1)
        (part,) = parts.values()
        self.assertEqual(part.type, "thinking")
        self.assertEqual(part.data["collapsed"], True)


# ── Projector: after_seq > last_seq ────────────────────────────────


class TestProjectorAfterSeqExceedsLast(unittest.TestCase):
    """Calling project() with after_seq > last_seq returns empty state."""

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

    def test_project_after_seq_exceeds_last(self) -> None:
        # Publish 3 events
        for i in range(1, 4):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i},
            ))
        # Project with after_seq much higher
        state = self.projector.project("s1", after_seq=100)
        # No events after seq=100, so no messages
        self.assertEqual(len(state.messages), 0)


# ── EventBusV2: replay after_seq > last_seq ──────────────────────


class TestEventBusV2ReplayAfterSeqExceeds(unittest.TestCase):
    """replay(after_seq > last_seq) returns []."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_replay_after_seq_exceeds(self) -> None:
        for i in range(1, 4):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {},
            ))
        events = self.v2.replay("s1", after_seq=100)
        self.assertEqual(events, [])


# ── EventBusV2: empty string in data ─────────────────────────────


class TestEventBusV2EmptyStringData(unittest.TestCase):
    """Empty string in data is valid JSON, should work."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_with_empty_string_data(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": "",  # empty string
        })
        self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 1)
        # Replay and verify
        events = self.v2.replay("s1")
        self.assertEqual(events[0].data["text"], "")

    def test_publish_with_empty_object_data(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
        self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 1)


# ── Projector: many parts in one message ──────────────────────────


class TestProjectorManyParts(unittest.TestCase):
    """A message with many parts (stress test)."""

    def test_50_text_parts_in_one_message(self) -> None:
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        # 50 text parts: each started+delta+ended
        seq = 0
        for i in range(50):
            text_id = f"t{i}"
            for et, et_data in [
                (EventType.TEXT_STARTED, {"text_id": text_id}),
                (EventType.TEXT_DELTA, {
                    "text_id": text_id, "text": f"part{i}",
                }),
                (EventType.TEXT_ENDED, {
                    "text_id": text_id, "text": f"part{i}",
                }),
            ]:
                seq += 1
                data = {"message_id": "a1"}
                data.update(et_data)
                projector.apply(
                    EventV2.create("s1", seq, et, data), state,
                )
        # 50 text parts
        self.assertEqual(len(state.messages["a1"].parts), 50)
        # All parts have correct text
        for i in range(50):
            text = state.messages["a1"].parts[f"t{i}"].data["text"]
            self.assertEqual(text, f"part{i}")

    def test_100_tool_calls_in_one_message(self) -> None:
        """100 parallel tool calls — verify all are tracked correctly."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        seq = 0
        for i in range(100):
            seq += 1
            projector.apply(EventV2.create("s1", seq, EventType.TOOL_CALL, {
                "message_id": "a1", "id": f"tc-{i}",
                "tool": "noop", "input": {"i": i},
            }), state)
            seq += 1
            projector.apply(EventV2.create("s1", seq, EventType.TOOL_RESULT, {
                "message_id": "a1", "id": f"tc-{i}",
                "result": str(i), "status": "done",
            }), state)
        self.assertEqual(len(state.messages["a1"].parts), 100)
        for i in range(100):
            part = state.messages["a1"].parts[f"tc-{i}"]
            self.assertEqual(part.data["result"], str(i))
            self.assertEqual(part.data["state"], "done")


# ── Projector: interleaved message_ids ───────────────────────────


class TestProjectorInterleavedMessages(unittest.TestCase):
    """Multiple assistant messages in the same stream (rare but possible)."""

    def test_interleaved_text_parts_two_messages(self) -> None:
        """text.started for m1, then for m2, then more for m1."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        events = [
            (1, EventType.TEXT_STARTED, {"message_id": "m1", "text_id": "t1"}),
            (2, EventType.TEXT_DELTA, {"message_id": "m1", "text_id": "t1", "text": "A"}),
            (3, EventType.TEXT_STARTED, {"message_id": "m2", "text_id": "t2"}),
            (4, EventType.TEXT_DELTA, {"message_id": "m2", "text_id": "t2", "text": "B"}),
            (5, EventType.TEXT_DELTA, {"message_id": "m1", "text_id": "t1", "text": "A2"}),
            (6, EventType.TEXT_DELTA, {"message_id": "m2", "text_id": "t2", "text": "B2"}),
        ]
        for seq, et, data in events:
            projector.apply(
                EventV2.create("s1", seq, et, data), state,
            )
        # Two messages, each with one text part
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages["m1"].parts["t1"].data["text"], "AA2")
        self.assertEqual(state.messages["m2"].parts["t2"].data["text"], "BB2")


# ── Projector: helper predicates on edge cases ────────────────────


class TestProjectorHelperPredicates(unittest.TestCase):
    """Edge cases for is_text_event, is_tool_event, is_thinking_event,
    is_message_lifecycle on EventV2."""

    def test_is_text_event_for_dot_notation(self) -> None:
        """TEXT_STARTED uses 'text.started' (dot), TEXT_DELTA uses
        'text_delta' (underscore). The projector checks both."""
        e1 = EventV2.create("s", 1, "text.started")
        e2 = EventV2.create("s", 1, "text_delta")
        e3 = EventV2.create("s", 1, "text.ended")
        self.assertTrue(e1.is_text_event())
        self.assertTrue(e2.is_text_event())
        self.assertTrue(e3.is_text_event())

    def test_predicate_for_unknown_type(self) -> None:
        """Unknown event type: all predicates are False."""
        e = EventV2.create("s", 1, "unknown.event.type")
        self.assertFalse(e.is_text_event())
        self.assertFalse(e.is_tool_event())
        self.assertFalse(e.is_thinking_event())
        self.assertFalse(e.is_message_lifecycle())


# ── EventBusV2: very long payload ─────────────────────────────────


class TestEventBusV2VeryLongPayload(unittest.TestCase):
    """Test publish with a very large data payload."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_1mb_payload(self) -> None:
        """1MB text in data round-trips through event_log."""
        big_text = "x" * 1_000_000
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": big_text,
        })
        self.v2.publish(e)
        self.assertEqual(self.v2.count("s1"), 1)
        # Replay and verify size
        events = self.v2.replay("s1")
        self.assertEqual(len(events[0].data["text"]), 1_000_000)


# ── Projector: state immutability of events ──────────────────────


class TestProjectorStateIsolation(unittest.TestCase):
    """Multiple Projector instances should not share state."""

    def test_two_projectors_independent(self) -> None:
        """Two Projector objects are stateless; the state is passed in."""
        p1 = Projector(Path("/tmp/nonexistent"))
        p2 = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1", "content": "hi",
        })
        s1 = ProjectedSession(session_id="s1")
        s2 = ProjectedSession(session_id="s1")
        p1.apply(e, s1)
        # p2 hasn't seen the event
        self.assertEqual(len(s2.messages), 0)
        # Now p2 applies the same event
        p2.apply(e, s2)
        # Both states have the same content (deterministic)
        self.assertEqual(
            s1.messages["u1"].content,
            s2.messages["u1"].content,
        )


# ── Projector: project() with no events at all ────────────────────


class TestProjectorEmptyProjection(unittest.TestCase):
    """Project a session that has no events."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_project_no_events(self) -> None:
        projector = Projector(self.db_path)
        state = projector.project("nonexistent-session")
        self.assertEqual(state.session_id, "nonexistent-session")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 0)

    def test_project_session_with_only_lifecycle_events(self) -> None:
        """session.created + iter_start + agent_done: no messages created."""
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path)
        v2.publish(EventV2.create("s1", 1, EventType.SESSION_CREATED, {}))
        v2.publish(EventV2.create("s1", 2, EventType.ITER_START, {}))
        v2.publish(EventV2.create("s1", 3, EventType.AGENT_DONE, {}))
        projector = Projector(self.db_path)
        state = projector.project("s1")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 3)


# ── EventV2: equality semantics ──────────────────────────────────


class TestEventV2Equality(unittest.TestCase):
    """Two EventV2 with same fields should be equal."""

    def test_same_fields_equal(self) -> None:
        e1 = EventV2(
            id="abc", aggregate_id="s1", seq=1, type="text_delta",
            data={"x": 1}, time_created=1.0,
        )
        e2 = EventV2(
            id="abc", aggregate_id="s1", seq=1, type="text_delta",
            data={"x": 1}, time_created=1.0,
        )
        self.assertEqual(e1, e2)

    def test_different_id_not_equal(self) -> None:
        e1 = EventV2(id="abc", aggregate_id="s1", seq=1, type="x", data={}, time_created=1.0)
        e2 = EventV2(id="def", aggregate_id="s1", seq=1, type="x", data={}, time_created=1.0)
        self.assertNotEqual(e1, e2)

    def test_different_data_not_equal(self) -> None:
        e1 = EventV2(id="abc", aggregate_id="s1", seq=1, type="x", data={"y": 1}, time_created=1.0)
        e2 = EventV2(id="abc", aggregate_id="s1", seq=1, type="x", data={"y": 2}, time_created=1.0)
        self.assertNotEqual(e1, e2)


# ── Projector: tool_call with deeply nested input ─────────────────


class TestProjectorDeeplyNestedInput(unittest.TestCase):
    """Tool calls can have deeply nested input arguments."""

    def test_tool_call_with_nested_input(self) -> None:
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        nested_input = {
            "level1": {
                "level2": {
                    "level3": [1, 2, {"level4": "deep"}],
                },
            },
        }
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1", "id": "tc-1",
            "tool": "complex", "input": nested_input,
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["input"], nested_input)


# ── EventBusV2: SSE forwarding with queue ─────────────────────────


class TestEventBusV2SSEForwardingWithQueue(unittest.TestCase):
    """Verify that published events can be consumed by an async subscriber."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        # Set up asyncio loop for the EventBus
        import asyncio
        self.loop = asyncio.new_event_loop()
        self.bus.set_loop(self.loop)
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.loop.close()
        self.db_path.unlink(missing_ok=True)

    def test_subscriber_receives_event(self) -> None:
        """A subscriber to the legacy EventBus receives the event
        when we publish via EventBusV2."""
        import asyncio

        async def subscribe_and_collect():
            received = []
            async def collect_one():
                async for event in self.bus.subscribe("s1", replay_all=True):
                    received.append(event)
                    if len(received) >= 1:
                        return
                    # Don't loop forever
                    break
            await asyncio.wait_for(collect_one(), timeout=2.0)
            return received

        # Publish a sync event first (it'll be in the buffer for replay)
        self.v2.publish(EventV2.create("s1", 1, EventType.TEXT_DELTA, {"hi": 1}))
        # Subscribe with replay_all=True to get the buffered event
        events = self.loop.run_until_complete(subscribe_and_collect())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "text_delta")
        self.assertEqual(events[0].data, {"hi": 1})


if __name__ == "__main__":
    unittest.main()
