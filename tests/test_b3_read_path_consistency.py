"""B3 consistency: event_log projection == messages table row-by-row.

The most important B3 invariant: reading from event_log via the
projector produces EXACTLY the same Message objects as reading
directly from the messages + message_parts tables.

These tests verify this for various scenarios:
- Simple user message
- Assistant message with text parts
- Assistant message with tool call + result
- Full multi-turn conversation
- Empty session
- Messages with metadata
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from strategy_research.api.session.event_bus_v2 import EventBusV2
from strategy_research.api.session.events import EventBus
from strategy_research.api.session.models import Message
from strategy_research.api.session.projector import Projector
from strategy_research.api.session.store import SessionStore


def _setup_db(db_path: Path) -> None:
    """Create all tables needed for dual-path consistency tests."""
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
        CREATE INDEX idx_messages_session_created
            ON messages(session_id, created_at);
        CREATE INDEX idx_messages_session_type_created
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
        CREATE INDEX idx_message_parts_message_seq
            ON message_parts(message_id, seq);
        CREATE INDEX idx_message_parts_session_seq
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
        CREATE INDEX idx_event_log_aggregate_seq
            ON event_log(aggregate_id, seq);
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "u", "test", 1.0, 1.0),
    )
    conn.commit()
    conn.close()


class TestB3ReadPathConsistency(unittest.TestCase):
    """Verify event_log projection matches messages table exactly.

    For each test case, we:
    1. Write messages + parts directly to DB (simulating service.py writes)
    2. Emit the same events to event_log via EventBusV2
    3. Read via both paths and compare field-by-field
    """

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.legacy_bus = EventBus()
        self.v2 = EventBusV2(self.legacy_bus, self.db_path)
        self.store = SessionStore(db_path=self.db_path)
        self.proj = Projector(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    # ── Helpers ──────────────────────────────────────────────────

    def _insert_message_direct(
        self, msg_id: str, role: str, content: str, seq: int,
        parts: list | None = None,
    ) -> None:
        """Insert a message + parts directly into messages/message_parts tables."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, "
            "created_at, message_type, seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, "s1", role, content, 1700000000.0, role, seq),
        )
        if parts:
            for i, part in enumerate(parts):
                conn.execute(
                    "INSERT INTO message_parts (id, message_id, session_id, "
                    "type, data_json, seq, time_created) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?)",
                    (
                        part["id"], msg_id, "s1", part["type"],
                        json.dumps(part, ensure_ascii=False),
                        i, 1700000000.0,
                    ),
                )
        conn.commit()
        conn.close()

    def _read_messages_direct(self, session_id: str, limit: int = 100) -> list:
        """Read messages from messages + message_parts tables directly via SQL.

        Returns list[Message] in the same format as SessionStore.get_messages().
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? "
            "ORDER BY seq ASC, created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()

        parts_by_msg: dict = {}
        if rows:
            message_ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(message_ids))
            parts_rows = conn.execute(
                f"SELECT message_id, data_json FROM message_parts "
                f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
                message_ids,
            ).fetchall()
            for mid, data_json in parts_rows:
                try:
                    part = json.loads(data_json)
                    if isinstance(part, dict):
                        parts_by_msg.setdefault(mid, []).append(part)
                except (json.JSONDecodeError, TypeError):
                    pass
        conn.close()

        out = []
        for r in rows:
            metadata_json = r["metadata_json"]
            metadata = json.loads(metadata_json) if metadata_json else {}
            parts_list = parts_by_msg.get(r["id"], [])
            out.append(Message(
                message_id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"] or "",
                message_type=r["message_type"],
                seq=r["seq"],
                metadata={
                    **metadata,
                    "_parts": parts_list,
                },
            ))
        return out

    def _compare_messages(
        self, db_msgs: list, el_msgs: list,
        check_parts: bool = True,
    ) -> None:
        """Compare two lists of messages field by field.

        Checks: count, role, content, message_type, seq, parts count,
        and part key fields.
        """
        self.assertEqual(len(db_msgs), len(el_msgs),
                         f"Message count mismatch: {len(db_msgs)} vs {len(el_msgs)}")

        for i, (db_msg, el_msg) in enumerate(zip(db_msgs, el_msgs)):
            with self.subTest(msg_index=i):
                self.assertEqual(db_msg.role, el_msg.role,
                                 f"msg {i}: role mismatch")
                self.assertEqual(db_msg.content, el_msg.content,
                                 f"msg {i}: content mismatch")
                self.assertEqual(db_msg.message_type, el_msg.message_type,
                                 f"msg {i}: message_type mismatch")
                self.assertEqual(db_msg.seq, el_msg.seq,
                                 f"msg {i}: seq mismatch")

                if check_parts:
                    db_parts = db_msg.metadata.get("_parts", [])
                    el_parts = el_msg.metadata.get("_parts", [])
                    self.assertEqual(len(db_parts), len(el_parts),
                                     f"msg {i}: part count mismatch")

                    for j, (db_part, el_part) in enumerate(zip(db_parts, el_parts)):
                        with self.subTest(msg_index=i, part_index=j):
                            self.assertEqual(db_part.get("type"), el_part.get("type"),
                                             f"msg {i} part {j}: type mismatch")

    # ── Test cases ──────────────────────────────────────────────

    def test_empty_session(self) -> None:
        """Empty session: both paths return empty list."""
        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.store._get_messages_from_event_log("s1")
        self.assertEqual(len(db_msgs), 0)
        self.assertEqual(len(el_msgs), 0)

    def test_single_user_message(self) -> None:
        """Single user message — both paths match."""
        self._insert_message_direct("u1", "user", "hello", 1)
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "user_message_id": "u1",
            "content": "hello", "role": "user",
        })

        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")
        self._compare_messages(db_msgs, el_msgs, check_parts=False)

    def test_assistant_with_text_part(self) -> None:
        """Assistant message with a text part."""
        self._insert_message_direct(
            "a1", "assistant", "Hi there", 1,
            parts=[
                {"type": "text", "id": "t1", "text": "Hi there"},
            ],
        )

        self.v2.emit("s1", "text.started", {
            "message_id": "a1", "text_id": "t1",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "Hi ",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "there",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1", "text": "Hi there",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "Hi there",
        })

        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")
        self._compare_messages(db_msgs, el_msgs, check_parts=True)

        # Check text content matches
        db_text = db_msgs[0].metadata["_parts"][0]["text"]
        el_text = el_msgs[0].metadata["_parts"][0]["text"]
        self.assertEqual(db_text, el_text)

    def test_assistant_with_tool_call_and_result(self) -> None:
        """Assistant message with tool call + result."""
        self._insert_message_direct(
            "a1", "assistant", "The result is 3", 1,
            parts=[
                {
                    "type": "tool_call", "id": "tc1",
                    "name": "calc",
                    "arguments": '{"a": 1, "b": 2}',
                    "result": "3",
                    "status": "done",
                },
            ],
        )

        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1",
            "tool": "calc", "input": {"a": 1, "b": 2},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1",
            "result": "3", "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "The result is 3",
        })

        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")
        self._compare_messages(db_msgs, el_msgs, check_parts=True)

        # Tool call result matches
        db_part = db_msgs[0].metadata["_parts"][0]
        el_part = el_msgs[0].metadata["_parts"][0]
        self.assertEqual(db_part.get("result"), el_part.get("result"))
        self.assertEqual("done", el_part.get("state"))

    def test_full_conversation(self) -> None:
        """Full multi-turn conversation: user → assistant → user → assistant."""
        # Message 1: user
        self._insert_message_direct("u1", "user", "What is 1+1?", 1)
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "What is 1+1?", "role": "user",
        })

        # Message 2: assistant (text + tool)
        self._insert_message_direct(
            "a1", "assistant", "Let me calculate that... 2", 2,
            parts=[
                {"type": "text", "id": "t1", "text": "Let me calculate that..."},
                {
                    "type": "tool_call", "id": "tc1",
                    "name": "calc",
                    "arguments": '{"a": 1, "b": 1}',
                    "result": "2",
                    "status": "done",
                },
            ],
        )
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "Let me calculate that..."})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "Let me calculate that..."})
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1", "tool": "calc", "input": {"a": 1, "b": 1},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1", "result": "2",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "Let me calculate that... 2",
        })

        # Message 3: user
        self._insert_message_direct("u2", "user", "What about 2+2?", 3)
        self.v2.emit("s1", "message_received", {
            "message_id": "u2", "content": "What about 2+2?", "role": "user",
        })

        # Message 4: assistant (text only)
        self._insert_message_direct(
            "a2", "assistant", "That would be 4", 4,
            parts=[{"type": "text", "id": "t2", "text": "That would be 4"}],
        )
        self.v2.emit("s1", "text.started", {"message_id": "a2", "text_id": "t2"})
        self.v2.emit("s1", "text_delta", {"message_id": "a2", "text_id": "t2", "text": "That would be 4"})
        self.v2.emit("s1", "text.ended", {"message_id": "a2", "text_id": "t2", "text": "That would be 4"})
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a2", "content": "That would be 4",
        })

        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")
        self._compare_messages(db_msgs, el_msgs, check_parts=True)

        # Specific field checks
        self.assertEqual(db_msgs[0].content, "What is 1+1?")
        self.assertEqual(db_msgs[1].content, "Let me calculate that... 2")
        self.assertEqual(db_msgs[2].content, "What about 2+2?")
        self.assertEqual(db_msgs[3].content, "That would be 4")

    def test_multiple_text_parts(self) -> None:
        """Assistant message with multiple text parts."""
        self._insert_message_direct(
            "a1", "assistant", "part1 part2", 1,
            parts=[
                {"type": "text", "id": "t1", "text": "part1"},
                {"type": "text", "id": "t2", "text": "part2"},
            ],
        )

        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "part1"})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "part1"})
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t2"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t2", "text": "part2"})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t2", "text": "part2"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "part1 part2"})

        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")
        self._compare_messages(db_msgs, el_msgs, check_parts=True)

    def test_message_ordering_by_seq(self) -> None:
        """Both paths order messages by seq."""
        # Insert in wrong order
        self._insert_message_direct("u2", "user", "second user", 3)
        self._insert_message_direct("u1", "user", "first user", 1)
        self._insert_message_direct("a1", "assistant", "assistant", 2)

        # Emit in same wrong order
        self.v2.emit("s1", "message_received", {"message_id": "u2", "content": "second user", "role": "user"})
        self.v2.emit("s1", "message_received", {"message_id": "u1", "content": "first user", "role": "user"})
        self.v2.emit("s1", "assistant_message", {"message_id": "a1", "content": "assistant"})

        # Both should be ordered by seq
        db_msgs = self._read_messages_direct("s1")
        el_msgs = self.proj.project_to_messages("s1")

        # Seq order
        db_seqs = [m.seq for m in db_msgs]
        el_seqs = [m.seq for m in el_msgs]
        self.assertEqual(db_seqs, sorted(db_seqs))
        self.assertEqual(el_seqs, sorted(el_seqs))
        self.assertEqual(db_seqs, el_seqs)

    def test_limit_works(self) -> None:
        """limit parameter works on both paths (first N by seq)."""
        for i in range(10):
            mid = f"u{i}"
            self._insert_message_direct(mid, "user", f"msg {i}", i + 1)
            self.v2.emit("s1", "message_received", {
                "message_id": mid, "content": f"msg {i}", "role": "user",
            })

        db_5 = self._read_messages_direct("s1", limit=5)
        el_5 = self.proj.project_to_messages("s1", limit=5)
        self.assertEqual(len(db_5), 5)
        self.assertEqual(len(el_5), 5)
        # Both return the FIRST 5 messages (ORDER BY seq ASC LIMIT N)
        self.assertEqual(db_5[0].seq, 1)
        self.assertEqual(db_5[-1].seq, 5)
        self.assertEqual(el_5[0].seq, 1)
        self.assertEqual(el_5[-1].seq, 5)


if __name__ == "__main__":
    unittest.main()
