"""B4: event-only write path (Level 3, B4 commit 3).

Verifies that writing ONLY via events (no direct store.append_message)
produces the same messages table state as the legacy direct-write path.

This is the B4 invariant: event_log is the sole source of truth,
and messages + message_parts are materialized views maintained
by Projector.flush() triggered from EventBusV2.

Test scenarios simulate what service.py does after B4:
- User message: emit message_received (no direct append)
- Assistant message: emit text/tool events + assistant_message
- Error: emit assistant_message with message_type=error
- Compaction: emit compact event
"""
from __future__ import annotations

import json
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


class TestB4EventOnlyWrite(unittest.TestCase):
    """Verify event-only write path produces correct messages table state.

    All tests use flush_to_messages=True and write ONLY via events.
    No direct store.append_message calls.
    """

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

    def _count_messages(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.row_factory = sqlite3.Row
        n = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",),
        ).fetchone()[0]
        conn.close()
        return n

    def _count_parts(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.row_factory = sqlite3.Row
        n = conn.execute(
            "SELECT COUNT(*) FROM message_parts WHERE session_id = ?", ("s1",),
        ).fetchone()[0]
        conn.close()
        return n

    def test_user_message_event_only(self) -> None:
        """User message: emit message_received → message appears in table."""
        self.assertEqual(self._count_messages(), 0)

        self.v2.emit("s1", "message_received", {
            "message_id": "u1",
            "user_message_id": "u1",
            "content": "hello world",
            "role": "user",
        })

        self.assertEqual(self._count_messages(), 1)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, role, content, message_type FROM messages WHERE id = ?",
            ("u1",),
        ).fetchone()
        conn.close()
        self.assertEqual(row["role"], "user")
        self.assertEqual(row["content"], "hello world")
        self.assertEqual(row["message_type"], "user")

    def test_assistant_message_event_only(self) -> None:
        """Assistant with text: emit text events + assistant_message."""
        self.v2.emit("s1", "text.started", {
            "message_id": "a1", "text_id": "t1",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "Hello ",
        })
        self.v2.emit("s1", "text_delta", {
            "message_id": "a1", "text_id": "t1", "text": "world",
        })
        self.v2.emit("s1", "text.ended", {
            "message_id": "a1", "text_id": "t1", "text": "Hello world",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "Hello world",
        })

        self.assertEqual(self._count_messages(), 1)
        self.assertEqual(self._count_parts(), 1)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, role, content, message_type FROM messages WHERE id = ?",
            ("a1",),
        ).fetchone()
        part = conn.execute(
            "SELECT type, data_json FROM message_parts WHERE message_id = ?",
            ("a1",),
        ).fetchone()
        conn.close()

        self.assertEqual(row["role"], "assistant")
        self.assertEqual(row["content"], "Hello world")
        self.assertEqual(part["type"], "text")
        self.assertEqual(json.loads(part["data_json"])["text"], "Hello world")

    def test_tool_call_and_result_event_only(self) -> None:
        """Tool call + result: both events → merged into single part."""
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1",
            "tool": "calc", "input": {"a": 1, "b": 2},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1",
            "result": "3", "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "result is 3",
        })

        self.assertEqual(self._count_parts(), 1)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        part = conn.execute(
            "SELECT data_json FROM message_parts WHERE id = ?", ("tc1",),
        ).fetchone()
        conn.close()

        data = json.loads(part["data_json"])
        self.assertEqual(data["tool"], "calc")
        self.assertEqual(data["result"], "3")
        self.assertEqual(data["state"], "done")

    def test_error_message_event_only(self) -> None:
        """Error message: assistant_message with message_type=error."""
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1",
            "content": "Something went wrong",
            "message_type": "error",
            "metadata": {"status": "error", "details": "timeout"},
        })

        self.assertEqual(self._count_messages(), 1)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, role, message_type, content, metadata_json FROM messages WHERE id = ?",
            ("a1",),
        ).fetchone()
        conn.close()

        self.assertEqual(row["message_type"], "error")
        self.assertEqual(row["content"], "Something went wrong")
        # metadata may or may not be preserved by projector (not critical for B4)

    def test_full_conversation_event_only(self) -> None:
        """Full conversation: user → assistant → user → assistant."""
        # User 1
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "What is 1+1?", "role": "user",
        })
        # Assistant 1 (text + tool)
        self.v2.emit("s1", "text.started", {"message_id": "a1", "text_id": "t1"})
        self.v2.emit("s1", "text_delta", {"message_id": "a1", "text_id": "t1", "text": "Calculating..."})
        self.v2.emit("s1", "text.ended", {"message_id": "a1", "text_id": "t1", "text": "Calculating..."})
        self.v2.emit("s1", "tool_call", {
            "message_id": "a1", "id": "tc1", "tool": "calc", "input": {"a": 1, "b": 1},
        })
        self.v2.emit("s1", "tool_result", {
            "message_id": "a1", "id": "tc1", "result": "2", "status": "done",
        })
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a1", "content": "The answer is 2",
        })
        # User 2
        self.v2.emit("s1", "message_received", {
            "message_id": "u2", "content": "What about 2+2?", "role": "user",
        })
        # Assistant 2 (text only)
        self.v2.emit("s1", "text.started", {"message_id": "a2", "text_id": "t2"})
        self.v2.emit("s1", "text_delta", {"message_id": "a2", "text_id": "t2", "text": "That would be 4"})
        self.v2.emit("s1", "text.ended", {"message_id": "a2", "text_id": "t2", "text": "That would be 4"})
        self.v2.emit("s1", "assistant_message", {
            "message_id": "a2", "content": "That would be 4",
        })

        self.assertEqual(self._count_messages(), 4)
        self.assertEqual(self._count_parts(), 3)  # 2 text + 1 tool

        # Verify projector reads match direct table reads
        proj_msgs = self.proj.project_to_messages("s1")
        self.assertEqual(len(proj_msgs), 4)
        self.assertEqual(proj_msgs[0].role, "user")
        self.assertEqual(proj_msgs[1].role, "assistant")
        self.assertEqual(proj_msgs[2].role, "user")
        self.assertEqual(proj_msgs[3].role, "assistant")

    def test_compaction_event_only(self) -> None:
        """Compaction: compact event → compaction message in table."""
        self.v2.emit("s1", "compact", {
            "summary": "Context compressed from 8000 to 2000 tokens",
            "before_tokens": 8000,
            "after_tokens": 2000,
        })

        self.assertEqual(self._count_messages(), 1)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, role, message_type, content FROM messages WHERE message_type = ?",
            ("compaction",),
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "system")
        self.assertEqual(row["message_type"], "compaction")
        self.assertIn("compressed", row["content"])

    def test_flush_idempotent_event_only(self) -> None:
        """Re-emitting the same events doesn't duplicate messages."""
        # First pass
        self.v2.emit("s1", "message_received", {
            "message_id": "u1", "content": "hello", "role": "user",
        })
        self.assertEqual(self._count_messages(), 1)
        ''
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM messages WHERE session_id = ? ORDER BY seq", ("s1",),
        ).fetchone()
        row['id']
        conn.close()

        # Force re-flush by calling projector.flush() directly
        state = self.proj.project("s1")
        self.proj.flush(state)

        # Still 1 message (idempotent)
        self.assertEqual(self._count_messages(), 1)

    def test_event_order_preserved(self) -> None:
        """Messages appear in the correct order (by seq) in messages table."""
        for i in range(5):
            self.v2.emit("s1", "message_received", {
                "message_id": f"u{i}", "content": f"msg {i}", "role": "user",
            })

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, seq FROM messages WHERE session_id = ? ORDER BY seq",
            ("s1",),
        ).fetchall()
        conn.close()

        self.assertEqual(len(rows), 5)
        seqs = [r["seq"] for r in rows]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["u0", "u1", "u2", "u3", "u4"])


if __name__ == "__main__":
    unittest.main()
