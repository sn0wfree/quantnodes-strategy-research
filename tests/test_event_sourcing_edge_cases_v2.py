"""More edge case tests for Phase 3 B1 components (round 2).

Gaps covered in this file (vs. existing test suites):
1. EventBusV2 error handling:
   - DB file doesn't exist
   - DB file is read-only
   - data is not JSON-serializable
2. Projector handlers for event types not yet tested:
   - session.created, session_meta_updated, attempt.created
   - queue_state, queue_paused
   - iter_start, iter_end
   - llm_usage, session_total_tokens
   - compact, compact.started, compact.ended
   - agent_done, error
3. Projector with multiple text parts in one message
4. EventV2 JSON special characters (quotes, backslashes, newlines)
5. EventBusV2 idempotency (same event published twice)
6. tool_call with function.arguments as string (LLM API style)
7. EventBusV2 with seq=0 (DB allows, EventV2.create rejects)
8. EventBusV2 performance baseline
9. ProjectedPart/Message dataclass equality
"""
from __future__ import annotations

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


# ── EventBusV2 error handling ─────────────────────────────────────


class TestEventBusV2ErrorHandling(unittest.TestCase):
    """Error handling: bad DB state, serialization failures."""

    def test_publish_to_nonexistent_db_creates_file(self) -> None:
        """If the DB doesn't exist, sqlite3.connect creates it.
        The publish will fail because the schema isn't there, but
        it should NOT crash with an unhandled exception."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        db_path.unlink()  # remove it
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
            # Should log error but not raise
            v2.publish(e)
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_publish_to_corrupt_db_doesnt_crash(self) -> None:
        """If the DB file is corrupted, publish should not raise."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        # Write garbage to make it a corrupt DB
        with open(db_path, "wb") as f:
            f.write(b"this is not a valid sqlite database")
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
            v2.publish(e)  # should not raise
        finally:
            db_path.unlink(missing_ok=True)

    def test_publish_with_non_json_serializable_data(self) -> None:
        """If data is not JSON-serializable, publish should log + skip."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            # Pass a set (not JSON-serializable) inside data
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
                "weird": {1, 2, 3},  # set, not JSON-serializable
            })
            # Should log error but not raise
            v2.publish(e)
            # No event should be persisted (json.dumps failed)
            self.assertEqual(v2.count("s1"), 0)
        finally:
            db_path.unlink(missing_ok=True)

    def test_publish_with_datetime_in_data(self) -> None:
        """datetime is not JSON-serializable by default."""
        from datetime import datetime
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        _setup_db(db_path)
        try:
            bus = EventBus()
            v2 = EventBusV2(bus, db_path)
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
                "time": datetime.now(),
            })
            v2.publish(e)  # should log + skip
            self.assertEqual(v2.count("s1"), 0)
        finally:
            db_path.unlink(missing_ok=True)

    def test_replay_with_nonexistent_db(self) -> None:
        """Replay against a non-existent DB returns []."""
        # Use a path that doesn't exist (parent dir exists, file doesn't)
        ghost_path = Path(tempfile.gettempdir()) / "ghost-db-never-existed.db"
        if ghost_path.exists():
            ghost_path.unlink()
        bus = EventBus()
        v2 = EventBusV2(bus, ghost_path)
        events = v2.replay("s1")
        self.assertEqual(events, [])
        self.assertEqual(v2.count("s1"), 0)
        self.assertEqual(v2.last_seq("s1"), 0)


# ── EventBusV2 idempotency ────────────────────────────────────────


class TestEventBusV2Idempotency(unittest.TestCase):
    """Verify what happens when the same event is published twice."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_same_event_twice(self) -> None:
        """Publishing the same EventV2 twice — what happens?

        First publish succeeds. Second publish hits UNIQUE (id) violation
        because event id is the PK. We log + skip.
        """
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {})
        self.v2.publish(e)  # OK
        # Same id, different content — should still hit PK violation
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {})
        # We need same id to test idempotency; recreate manually
        from dataclasses import replace
        e2_same_id = replace(e2, id=e.id, seq=1)
        self.v2.publish(e2_same_id)  # PK collision on id
        # Only one event in event_log
        self.assertEqual(self.v2.count("s1"), 1)

    def test_publish_same_seq_different_ids(self) -> None:
        """Same seq, different ids — UNIQUE (aggregate_id, seq) violation."""
        e1 = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"a": 1})
        e2 = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"a": 2})
        # Different ids (UUIDs are unique), same seq
        self.assertNotEqual(e1.id, e2.id)
        self.v2.publish(e1)  # OK
        self.v2.publish(e2)  # UNIQUE violation, log + skip
        self.assertEqual(self.v2.count("s1"), 1)


# ── Projector handlers for untested event types ───────────────────


class TestProjectorSessionLifecycleHandlers(unittest.TestCase):
    """Projector should accept (and ignore) session lifecycle events
    without crashing. They are not stored as parts but the projector
    must not error out."""

    def setUp(self) -> None:
        self.state = ProjectedSession(session_id="s1")
        self.projector = Projector(Path("/tmp/nonexistent"))

    def test_session_created_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.SESSION_CREATED, {
            "session_id": "s1", "title": "test",
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_session_meta_updated_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.SESSION_META_UPDATED, {
            "title": "renamed",
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_attempt_created_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.ATTEMPT_CREATED, {
            "attempt_id": "a1", "prompt": "hi",
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_queue_state_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.QUEUE_STATE, {
            "queue_length": 3,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_queue_paused_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.QUEUE_PAUSED, {
            "session_id": "s1",
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_iter_start_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.ITER_START, {
            "iteration": 1, "max_iterations": 5,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_iter_end_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.ITER_END, {
            "iteration": 1,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_llm_usage_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.LLM_USAGE, {
            "input_tokens": 100, "output_tokens": 50,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_session_total_tokens_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.SESSION_TOTAL_TOKENS, {
            "total_tokens": 1000,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_compact_creates_compaction_message(self) -> None:
        e = EventV2.create("s1", 1, EventType.COMPACT, {
            "summary": "compacted to 3 layers",
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 1)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.message_type, "compaction")
        self.assertIn("3 layers", msg.content)

    def test_compact_started_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.COMPACT_STARTED, {})
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)

    def test_compact_ended_creates_compaction_message(self) -> None:
        e = EventV2.create("s1", 1, EventType.COMPACT_ENDED, {
            "summary": "compaction finished",
            "before_tokens": 8000,
            "after_tokens": 2000,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 1)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.message_type, "compaction")
        self.assertEqual(msg.content, "compaction finished")

    def test_agent_done_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.AGENT_DONE, {
            "message_id": "a1", "status": "success",
        })
        self.projector.apply(e, self.state)
        # The message_id is "a1" — if projector creates a message for it
        # that's an interesting behavior to note.
        # Currently projector only creates messages for explicit
        # message_received and assistant_message events, so:
        self.assertEqual(len(self.state.messages), 0)

    def test_error_skipped(self) -> None:
        e = EventV2.create("s1", 1, EventType.AGENT_ERROR, {
            "message": "boom", "fatal": True,
        })
        self.projector.apply(e, self.state)
        self.assertEqual(len(self.state.messages), 0)


# ── Projector: multiple text parts in one message ─────────────────


class TestProjectorMultipleTextParts(unittest.TestCase):
    """A single message can have multiple text parts (e.g., text
    streamed before/after a tool call)."""

    def test_two_text_parts_in_one_message(self) -> None:
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))

        events = [
            EventV2.create("s1", 1, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            EventV2.create("s1", 2, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Let me check. ",
            }),
            EventV2.create("s1", 3, EventType.TOOL_CALL, {
                "message_id": "a1", "id": "tc-1",
                "tool": "fetch", "input": {},
            }),
            EventV2.create("s1", 4, EventType.TOOL_RESULT, {
                "message_id": "a1", "id": "tc-1",
                "result": "42", "status": "done",
            }),
            EventV2.create("s1", 5, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t2",  # NEW text_id
            }),
            EventV2.create("s1", 6, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t2", "text": "Got it: 42",
            }),
        ]
        for e in events:
            projector.apply(e, state)

        a = state.messages["a1"]
        # 2 text parts + 1 tool_call part = 3 parts
        self.assertEqual(len(a.parts), 3)
        self.assertIn("t1", a.parts)
        self.assertIn("t2", a.parts)
        self.assertIn("tc-1", a.parts)

        # Text parts in seq order: t1, tc-1, t2
        ordered = a.parts_in_order()
        self.assertEqual([p.id for p in ordered], ["t1", "tc-1", "t2"])
        self.assertEqual(a.parts["t1"].data["text"], "Let me check. ")
        self.assertEqual(a.parts["t2"].data["text"], "Got it: 42")
        self.assertEqual(a.parts["tc-1"].data["result"], "42")


# ── Projector: tool_call with function.arguments as string ───────


class TestProjectorToolCallStringArguments(unittest.TestCase):
    """OpenAI LLM API style: tool_call.function.arguments is a JSON string,
    not a parsed dict. The projector should preserve it as-is."""

    def test_tool_call_with_string_arguments(self) -> None:
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        # The service.py event_callback may pass arguments as a string
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1",
            "id": "tc-1",
            "function": {
                "name": "get_market_data",
                "arguments": '{"symbol": "000001.SZ", "limit": 100}',
            },
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        # The data should preserve the function arguments as a string
        self.assertIn("function", part.data)
        self.assertEqual(
            part.data["function"]["arguments"],
            '{"symbol": "000001.SZ", "limit": 100}',
        )

    def test_tool_call_with_flat_arguments(self) -> None:
        """The event_callback sometimes flattens tool/input."""
        state = ProjectedSession(session_id="s1")
        projector = Projector(Path("/tmp/nonexistent"))
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "a1",
            "id": "tc-1",
            "tool": "calc",
            "input": {"x": 1, "y": 2},
        })
        projector.apply(e, state)
        part = state.messages["a1"].parts["tc-1"]
        self.assertEqual(part.data["tool"], "calc")
        self.assertEqual(part.data["input"], {"x": 1, "y": 2})


# ── EventV2 JSON special characters ──────────────────────────────


class TestEventV2SpecialCharacters(unittest.TestCase):
    """Verify EventV2 round-trips data with special characters."""

    def test_double_quotes_in_string(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": 'He said "Hello"',
        })
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data["text"], 'He said "Hello"')

    def test_backslashes(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "path": "C:\\Users\\test",
        })
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data["path"], "C:\\Users\\test")

    def test_newlines_and_tabs(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": "line1\nline2\ttabbed",
        })
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data["text"], "line1\nline2\ttabbed")

    def test_unicode_escape(self) -> None:
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": "\u4f60\u597d",  # 你好
        })
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data["text"], "你好")

    def test_null_bytes(self) -> None:
        """Null bytes in strings are valid JSON but may cause issues
        with some consumers. Just verify the round-trip works."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {
            "text": "before\x00after",
        })
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.data["text"], "before\x00after")

    def test_json_special_chars_in_event_type(self) -> None:
        """Event type with special chars (unusual but allowed)."""
        e = EventV2.create("s1", 1, "weird/event.type", {"x": 1})
        s = e.to_json()
        e2 = EventV2.from_json(s)
        self.assertEqual(e2.type, "weird/event.type")


# ── EventV2 boundary values ────────────────────────────────────────


class TestEventV2BoundaryValues(unittest.TestCase):
    """Edge cases for field values."""

    def test_seq_zero_rejected(self) -> None:
        """EventV2.create rejects seq=0; DB allows it (default)."""
        with self.assertRaises(ValueError):
            EventV2.create("s1", 0, EventType.TEXT_DELTA)

    def test_very_large_seq(self) -> None:
        """seq can be any positive integer (no overflow)."""
        big_seq = 2**31  # ~2 billion
        e = EventV2.create("s1", big_seq, EventType.TEXT_DELTA)
        self.assertEqual(e.seq, big_seq)
        # Round-trip
        e2 = EventV2.from_json(e.to_json())
        self.assertEqual(e2.seq, big_seq)

    def test_very_long_aggregate_id(self) -> None:
        long_id = "x" * 1000
        e = EventV2.create(long_id, 1, EventType.TEXT_DELTA)
        self.assertEqual(e.aggregate_id, long_id)

    def test_time_created_can_be_overridden(self) -> None:
        """time_created can be set to any float."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA)
        e2 = EventV2(
            id=e.id, aggregate_id="s1", seq=1,
            type=EventType.TEXT_DELTA, data={},
            time_created=1234567890.123,
        )
        self.assertEqual(e2.time_created, 1234567890.123)

    def test_event_v2_str_repr(self) -> None:
        """EventV2 should have a useful repr for debugging."""
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA, {"text": "hi"})
        r = repr(e)
        # Just verify it doesn't crash and contains key info
        self.assertIn("s1", r)
        self.assertIn("text_delta", r)


# ── ProjectedPart/Message equality ────────────────────────────────


class TestProjectedStateEquality(unittest.TestCase):
    """Verify ProjectedPart/Message equality and serialization."""

    def test_part_equality(self) -> None:
        p1 = ProjectedPart(id="p1", type="text", data={"text": "hi"})
        p2 = ProjectedPart(id="p1", type="text", data={"text": "hi"})
        p3 = ProjectedPart(id="p1", type="text", data={"text": "bye"})
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)

    def test_part_to_dict(self) -> None:
        p = ProjectedPart(
            id="p1", type="text", data={"text": "hi"},
            seq=2, time_created=1.0,
        )
        d = p.to_dict()
        self.assertEqual(d, {
            "id": "p1", "type": "text",
            "data": {"text": "hi"}, "seq": 2, "time_created": 1.0,
        })

    def test_message_equality(self) -> None:
        m1 = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi",
        )
        m2 = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi",
        )
        m3 = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="bye",
        )
        self.assertEqual(m1, m2)
        self.assertNotEqual(m1, m3)

    def test_message_with_parts_equality(self) -> None:
        m1 = ProjectedMessage(
            id="m1", session_id="s1", role="assistant", content="",
        )
        m1.parts["p1"] = ProjectedPart(id="p1", type="text", data={"text": "x"})

        m2 = ProjectedMessage(
            id="m1", session_id="s1", role="assistant", content="",
        )
        m2.parts["p1"] = ProjectedPart(id="p1", type="text", data={"text": "x"})

        self.assertEqual(m1, m2)


# ── Projector messages_in_order tie-breaking ──────────────────────


class TestProjectorMessageOrdering(unittest.TestCase):
    """Test message ordering with tied seq values."""

    def test_messages_in_order_tie_break_by_created_at(self) -> None:
        """If two messages have the same seq, the older one (lower
        created_at) wins. This is a documented tie-breaking rule."""
        s = ProjectedSession(session_id="s1")
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="", seq=1,
            created_at=2.0,
        )
        s.messages["m2"] = ProjectedMessage(
            id="m2", session_id="s1", role="assistant", content="", seq=1,
            created_at=1.0,  # earlier
        )
        ordered = s.messages_in_order()
        # m2 has lower created_at, comes first
        self.assertEqual(ordered[0].id, "m2")
        self.assertEqual(ordered[1].id, "m1")

    def test_to_message_rows_with_same_seq_different_timestamps(self) -> None:
        s = ProjectedSession(session_id="s1")
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="A", seq=1,
            created_at=1.0,
        )
        s.messages["m2"] = ProjectedMessage(
            id="m2", session_id="s1", role="assistant", content="B", seq=1,
            created_at=2.0,
        )
        rows = s.to_message_rows()
        # Both are in the output, in (seq, created_at) order
        self.assertEqual(rows[0]["content"], "A")
        self.assertEqual(rows[1]["content"], "B")


# ── Projector end-to-end with realistic event sequences ────────────


class TestProjectorE2EWithMixedEventTypes(unittest.TestCase):
    """End-to-end: many event types in a realistic sequence."""

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

    def test_full_session_lifecycle(self) -> None:
        """session.created → message_received → ... → agent_done"""
        events = [
            (1, EventType.SESSION_CREATED, {"session_id": "s1", "title": "t"}),
            (2, EventType.MESSAGE_RECEIVED, {
                "message_id": "u1", "role": "user", "content": "Hello",
            }),
            (3, EventType.TEXT_STARTED, {
                "message_id": "a1", "text_id": "t1",
            }),
            (4, EventType.TEXT_DELTA, {
                "message_id": "a1", "text_id": "t1", "text": "Hi!",
            }),
            (5, EventType.TEXT_ENDED, {
                "message_id": "a1", "text_id": "t1", "text": "Hi!",
            }),
            (6, EventType.ASSISTANT_MESSAGE, {
                "message_id": "a1", "content": "Hi!",
            }),
            (7, EventType.AGENT_DONE, {"message_id": "a1", "status": "success"}),
        ]
        for seq, etype, data in events:
            self.v2.publish(EventV2.create("s1", seq, etype, data))

        state = self.projector.project("s1")
        # 2 messages: user and assistant
        self.assertEqual(len(state.messages), 2)
        # session.created, iter_start, agent_done don't create messages
        self.assertIn("u1", state.messages)
        self.assertIn("a1", state.messages)
        # last_seq reflects the highest seq we published
        self.assertEqual(state.last_seq, 7)
        # event_log has all 7 events
        self.assertEqual(self.v2.count("s1"), 7)

    def test_projector_with_only_non_message_events(self) -> None:
        """If no message_received/assistant_message events, no messages
        are created. session lifecycle events alone produce empty state."""
        events = [
            (1, EventType.SESSION_CREATED, {"session_id": "s1"}),
            (2, EventType.ATTEMPT_CREATED, {"attempt_id": "a1"}),
            (3, EventType.ITER_START, {"iteration": 1}),
            (4, EventType.LLM_USAGE, {"input_tokens": 100}),
            (5, EventType.ITER_END, {"iteration": 1}),
            (6, EventType.AGENT_DONE, {"status": "success"}),
        ]
        for seq, etype, data in events:
            self.v2.publish(EventV2.create("s1", seq, etype, data))

        state = self.projector.project("s1")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 6)


# ── EventBusV2 performance baseline ───────────────────────────────


class TestEventBusV2Performance(unittest.TestCase):
    """Basic performance check — just a smoke test, not a benchmark."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_publish_1000_events_under_5_seconds(self) -> None:
        """Smoke test: 1000 events should publish in <5 seconds.

        This is not a strict benchmark; it just catches major
        performance regressions (e.g., O(n²) loops in persist).
        """
        start = time.time()
        for i in range(1, 1001):
            self.v2.publish(EventV2.create(
                "s1", i, EventType.TEXT_DELTA, {"i": i}
            ))
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0, f"1000 publishes took {elapsed:.2f}s")
        self.assertEqual(self.v2.count("s1"), 1000)


# ── Cascade delete verification ───────────────────────────────────


class TestCascadeDeleteWithProjector(unittest.TestCase):
    """Verify that when a session is deleted, all events are gone,
    and the projector then sees an empty state."""

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

    def test_delete_session_cascades_events(self) -> None:
        """After DELETE FROM sessions, event_log is empty too."""
        # Publish some events
        for i in range(1, 4):
            self.v2.publish(EventV2.create("s1", i, EventType.TEXT_DELTA, {}))
        self.assertEqual(self.v2.count("s1"), 3)

        # Delete the session (must enable PRAGMA foreign_keys on this conn)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("DELETE FROM sessions WHERE id = ?", ("s1",))
            conn.commit()
        finally:
            conn.close()

        # All events should be gone (CASCADE)
        self.assertEqual(self.v2.count("s1"), 0)
        # Projector sees empty state
        state = self.projector.project("s1")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 0)


if __name__ == "__main__":
    unittest.main()
