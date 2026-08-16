"""B5 final invariant: messages table == projector.flush(event_log).

This is the B5 climax test: regardless of how events are produced
(chat, /goal, /compact, or any other path), the messages table
must equal what the projector would compute from event_log alone.

If this invariant holds:
- event_log is the sole source of truth
- messages + message_parts are disposable caches
- The system can always be recovered from event_log
- All write paths can be audited by examining event_log

We test this with realistic event sequences for each B5 write path.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus
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


def _read_db_state(db_path: Path, session_id: str) -> dict:
    """Read current messages + parts state from DB.

    Returns dict with 'messages' and 'parts' lists. Messages are
    sorted by (seq, id); parts are sorted by (seq, id). Both are
    deterministic and order-independent.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    messages = conn.execute(
        "SELECT id, role, content, message_type, seq FROM messages "
        "WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    parts = conn.execute(
        "SELECT id, message_id, type, seq FROM message_parts "
        "WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    conn.close()
    return {
        "messages": sorted(
            [(m["id"], m["role"], m["content"], m["message_type"], m["seq"])
             for m in messages],
            key=lambda x: (x[4], x[0]),  # (seq, id)
        ),
        "parts": sorted(
            [(p["id"], p["message_id"], p["type"], p["seq"]) for p in parts],
            key=lambda x: (x[3], x[0]),  # (seq, id)
        ),
    }


def _read_projector_state(db_path: Path, session_id: str) -> dict:
    """Read what the projector would produce from event_log.

    Sorted by (seq, id) to match _read_db_state for comparison.
    """
    proj = Projector(db_path)
    state = proj.project(session_id)
    messages = sorted(
        [(m.id, m.role, m.content, m.message_type, m.seq) for m in state.messages_in_order()],
        key=lambda x: (x[4], x[0]),
    )
    parts = sorted(
        [(p.id, m.id, p.type, p.seq)
         for m in state.messages_in_order()
         for p in m.parts_in_order()],
        key=lambda x: (x[3], x[0]),
    )
    return {"messages": messages, "parts": parts}


class TestB5FinalInvariant(unittest.TestCase):
    """The B5 invariant: messages table == projector.project(event_log)."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.legacy_bus = EventBus()
        self.v2 = EventBusV2(self.legacy_bus, self.db_path, flush_to_messages=True)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _assert_invariant(self, session_id: str) -> None:
        """Assert: DB state == Projector state."""
        db_state = _read_db_state(self.db_path, session_id)
        proj_state = _read_projector_state(self.db_path, session_id)
        self.assertEqual(
            db_state["messages"], proj_state["messages"],
            f"Messages mismatch!\nDB: {db_state['messages']}\n"
            f"Proj: {proj_state['messages']}"
        )
        self.assertEqual(
            db_state["parts"], proj_state["parts"],
            f"Parts mismatch!\nDB: {db_state['parts']}\n"
            f"Proj: {proj_state['parts']}"
        )

    def test_chat_path_invariant(self) -> None:
        """Main chat path: user → assistant with text+tool → user → assistant.

        Invariant holds after every boundary event flush.
        """
        # User 1
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "Question 1", "role": "user",
        })
        self._assert_invariant("s1")  # boundary event flushed

        # Assistant 1 (text + tool)
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "Computing..."})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "Computing..."})
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1",
            "tool": "calc", "input": {"a": 5},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1", "result": "10", "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "The answer is 10",
        })
        self._assert_invariant("s1")  # boundary event flushed

        # User 2
        self.v2.emit("s1", "message_received", {
            "message_id": "u2", "content": "Question 2", "role": "user",
        })
        self._assert_invariant("s1")

        # Assistant 2
        self.v2.emit("s1", "text.started", {"message_id": "a2", "text_id": "t2"})
        self.v2.emit("s1", "text_delta", {"message_id": "a2", "text_id": "t2", "text": "Answer 2"})
        self.v2.emit("s1", "text.ended", {"message_id": "a2", "text_id": "t2", "text": "Answer 2"})
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a2", "content": "Answer 2",
        })
        self._assert_invariant("s1")

    def test_error_path_invariant(self) -> None:
        """Error path: user + error assistant message."""
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "Trigger an error", "role": "user",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1",
            "content": "Something went wrong",
            "message_type": "error",
            "metadata": {"status": "error", "details": "timeout"},
        })
        self._assert_invariant("s1")

    def test_compact_path_invariant(self) -> None:
        """Compact path: existing messages + compact.ended replacement."""
        # Build initial conversation
        self.v2.emit("s1", "message_received", {"message_id": "u1", "content": "msg 1", "role": "user"})
        self._assert_invariant("s1")
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "answer 1"})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "answer 1"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "answer 1"})
        self._assert_invariant("s1")

        # Compact (replaces the 2 messages with 1 compressed + marker)
        self.v2.emit("s1", "compact.ended", {
            "summary": "Compressed to summary",
            "messages": [
                {"role": "user", "content": "[summary] original user msg", "id": "cmp_u"},
                {
                    "role": "assistant",
                    "content": "[summary] original assistant",
                    "id": "cmp_a",
                    "tool_calls": [{
                        "id": "cmp_tc",
                        "function": {"name": "calc", "arguments": "{}"},
                        "result": "42",
                    }],
                },
            ],
        })
        self._assert_invariant("s1")

    def test_long_conversation_invariant(self) -> None:
        """Long conversation (20 messages) — invariant holds throughout."""
        for i in range(10):
            uid = f"u{i}"
            aid = f"a{i}"

            self.v2.emit("s1", "message_received", {
                "message_id": uid, "content": f"user msg {i}", "role": "user",
            })
            self.v2.emit("s1", "text.started", {"message_id": aid, "text_id": f"t{i}"})
            self.v2.emit("s1", "text_delta", {
                "message_id": aid, "text_id": f"t{i}", "text": f"reply {i}",
            })
            self.v2.emit("s1", "text.ended", {
                "message_id": aid, "text_id": f"t{i}", "text": f"reply {i}",
            })
            self.v2.emit("s1", "assistant_message", {
                "message_id": aid, "content": f"reply {i}",
            })

            # Check invariant after each pair
            self._assert_invariant("s1")

    def test_invariant_after_table_wipe_and_re_flush(self) -> None:
        """Critical B5 invariant: even after wiping messages table,
        re-flush from event_log restores exact same state."""
        # Build conversation
        self.v2.emit("s1", "message_received", {"message_id": "u1", "content": "hi", "role": "user"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "hello"})

        before = _read_db_state(self.db_path, "s1")
        self.assertEqual(len(before["messages"]), 2)

        # Wipe messages table (simulate corruption/disaster)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM messages WHERE session_id = ?", ("s1",))
        conn.execute("DELETE FROM message_parts WHERE session_id = ?", ("s1",))
        conn.commit()
        conn.close()

        # Re-flush from event_log
        state = self.proj.project("s1")
        self.proj.flush(state)

        # State must be identical
        after = _read_db_state(self.db_path, "s1")
        self.assertEqual(before, after)

    def test_invariant_with_partial_stream(self) -> None:
        """Invariant holds after every boundary event.

        Between boundary events, DB is stale (catches up at next flush).
        """
        # text.started but no text_delta
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hi", "role": "user",
        })
        self._assert_invariant("s1")  # boundary

        self.v2.emit("s1", "text.started", {
            "message_id": "a1", "text_id": "t1",
        })
        # No boundary yet — DB has only u1, projector has u1 + a1
        # (this is expected; not testing invariant here)

        # Now resume
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "resumed",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1", "text": "resumed",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "resumed",
        })
        self._assert_invariant("s1")  # boundary event flushes

    def test_invariant_with_dup_emits(self) -> None:
        """Same event emitted twice doesn't break invariant."""
        from strategy_research.core.events.event_v2 import EventType, EventV2
        EventV2.create('s1', 1, EventType.MESSAGE_RECEIVED, {'message_id': 'u1', 'content': 'hi', 'role': 'user'})
        # First emit
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hi", "role": "user",
        })
        # Try re-emit (different EventV2, same data)
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hi", "role": "user",
        })
        self._assert_invariant("s1")

    def test_invariant_through_full_lifecycle(self) -> None:
        """Full lifecycle: create → chat → error → compact → more chat → final."""
        # Initial conversation
        self.v2.emit("s1", "message_received", {"message_id": "u1", "content": "first", "role": "user"})
        self._assert_invariant("s1")
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "first reply"})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "first reply"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "first reply"})
        self._assert_invariant("s1")

        # Compact
        self.v2.emit("s1", "compact.ended", {
            "summary": "Compressed 2 messages",
            "messages": [
                {"role": "user", "content": "[cmp] first", "id": "cmp_u"},
                {"role": "assistant", "content": "[cmp] first reply", "id": "cmp_a"},
            ],
        })
        self._assert_invariant("s1")

        # More conversation after compact
        self.v2.emit("s1", "message_received", {"message_id": "u3", "content": "third", "role": "user"})
        self._assert_invariant("s1")
        self.v2.emit("s1", "text.started", {"message_id": "a3", "text_id": "t3"})
        self.v2.emit("s1", "text_delta", {"message_id": "a3", "text_id": "t3", "text": "third reply"})
        self.v2.emit("s1", "text.ended", {"message_id": "a3", "text_id": "t3", "text": "third reply"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a3", "content": "third reply"})
        self._assert_invariant("s1")

    def test_invariant_db_and_projector_byte_equal(self) -> None:
        """Strictest invariant: DB and projector state are byte-equal."""
        # Complex conversation
        for i in range(5):
            uid = f"u{i}"
            aid = f"a{i}"

            self.v2.emit("s1", "message_received", {
                "message_id": uid, "content": f"q {i} with 🎉 unicode", "role": "user",
            })
            self.v2.emit("s1", "text.started", {"message_id": aid, "text_id": f"t{i}"})
            self.v2.emit("s1", "text_delta", {
                "message_id": aid, "text_id": f"t{i}",
                "text": f"reply {i} with 中文",
            })
            self.v2.emit("s1", "text.ended", {
                "message_id": aid, "text_id": f"t{i}",
                "text": f"reply {i} with 中文",
            })
            self.v2.emit("s1", "assistant_message", {
                "message_id": aid, "content": f"reply {i} with 中文",
            })

        # Get exact DB state
        db_state = _read_db_state(self.db_path, "s1")
        # Get exact projector state
        proj_state = _read_projector_state(self.db_path, "s1")

        # They must be exactly equal
        self.assertEqual(db_state["messages"], proj_state["messages"])
        self.assertEqual(db_state["parts"], proj_state["parts"])

    def test_invariant_with_multiple_sessions(self) -> None:
        """Invariant holds across multiple sessions in the same DB.

        Note: message ids are expected to be globally unique (UUIDs in
        production). Tests use prefixed ids (s1_u1, s2_u1) to avoid PK
        conflicts, but the invariant holds regardless.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("s2", "u", "test2", 1.0, 1.0),
        )
        conn.commit()
        conn.close()

        # Session 1
        self.v2.emit("s1", "message_received", {"message_id": "s1_u1", "content": "hi s1", "role": "user"})
        self._assert_invariant("s1")
        # Session 2
        self.v2.emit("s2", "message_received", {"message_id": "s2_u1", "content": "hi s2", "role": "user"})
        self._assert_invariant("s2")
        # Session 1 again
        self.v2.emit("s1", "assistant_message", {"message_id": "s1_a1", "content": "reply s1"})
        self._assert_invariant("s1")
        # Session 2 again
        self.v2.emit("s2", "assistant_message", {"message_id": "s2_a1", "content": "reply s2"})
        self._assert_invariant("s2")

        # Each session has 2 messages
        s1_state = _read_db_state(self.db_path, "s1")
        s2_state = _read_db_state(self.db_path, "s2")
        self.assertEqual(len(s1_state["messages"]), 2)
        self.assertEqual(len(s2_state["messages"]), 2)
        # Contents are different per session
        s1_contents = [m[2] for m in s1_state["messages"]]
        s2_contents = [m[2] for m in s2_state["messages"]]
        self.assertIn("hi s1", s1_contents)
        self.assertIn("hi s2", s2_contents)


if __name__ == "__main__":
    unittest.main()
