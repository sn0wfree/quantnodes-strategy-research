"""Tests for the message_parts backfill script (Level 2, commit 3)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

SRC_PATH = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_PATH))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    import strategy_research.api.routers.web_session as ws
    monkeypatch.setattr(ws, "_get_db_path", lambda: db_path)
    yield db_path


def _ensure_schema(db_path: Path) -> None:
    import strategy_research.api.routers.web_session as ws
    with ws._get_db() as conn:
        ws._ensure_schema(conn)


def _create_session(db_path: Path, sid: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "anonymous", "test", time.time(), time.time()),
        )
        conn.commit()


def _insert_message_with_parts(db_path: Path, sid: str, parts: list, ts: float = 100.0) -> str:
    """Insert a message with parts_json (legacy path)."""
    import uuid
    mid = str(uuid.uuid4())
    parts_json = json.dumps(parts, ensure_ascii=False)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json, message_type, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, sid, "assistant", "x", parts_json, None, ts, None, "assistant", 1),
        )
        conn.commit()
    return mid


class TestBackfillMessageParts:
    def test_dry_run_no_changes(self, temp_db):
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [{"type": "text", "text": "hi"}, {"type": "tool_call", "name": "x"}],
        )

        before = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert before == 0

        bk.backfill(db_path, apply=False)

        after = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        # dry-run didn't write
        assert after == 0

    def test_apply_migrates_parts_json(self, temp_db):
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [
                {"type": "text", "text": "hello"},
                {"type": "tool_call", "id": "tc1", "name": "x", "arguments": "{}"},
                {"type": "tool_call", "id": "tc2", "name": "y", "arguments": "{}"},
            ],
        )

        bk.backfill(db_path, apply=True)

        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT type, seq, data_json FROM message_parts ORDER BY seq"
            ).fetchall()
        assert len(rows) == 3
        assert [r[0] for r in rows] == ["text", "tool_call", "tool_call"]
        assert [r[1] for r in rows] == [0, 1, 2]
        # data_json round-trips
        assert json.loads(rows[0][2])["text"] == "hello"

    def test_idempotent_rerun(self, temp_db):
        """Running twice doesn't duplicate or error."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        _insert_message_with_parts(
            db_path, "sess-1",
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        )

        bk.backfill(db_path, apply=True)
        n1 = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert n1 == 2

        bk.backfill(db_path, apply=True)  # rerun
        n2 = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        # No duplicates: (message_id, seq) is unique
        assert n2 == 2

    def test_skips_messages_with_null_parts(self, temp_db):
        """Messages with parts_json=NULL or '[]' are skipped."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")
        # Insert with parts_json=null
        import uuid
        mid = str(uuid.uuid4())
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (mid, "sess-1", "user", "x", None, 100.0, None, "user", 1),
            )
            conn.commit()

        bk.backfill(db_path, apply=True)
        n = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert n == 0

    def test_skips_invalid_json(self, temp_db):
        """Messages with malformed parts_json are logged + skipped, not aborted."""
        from scripts import backfill_message_parts as bk
        db_path = temp_db
        _ensure_schema(db_path)
        _create_session(db_path, "sess-1")

        # Insert with invalid parts_json
        import uuid
        mid = str(uuid.uuid4())
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, 'not valid json{{', ?, ?, ?, ?, ?)",
                (mid, "sess-1", "assistant", "x", None, 100.0, None, "assistant", 1),
            )
            # And a valid one
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, parts_json, tool_call_id, created_at, metadata_json, message_type, seq) "
                "VALUES (?, ?, ?, ?, '[{\"type\": \"text\", \"text\": \"ok\"}]', ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "sess-1", "assistant", "y", None, 100.0, None, "assistant", 2),
            )
            conn.commit()

        bk.backfill(db_path, apply=True)
        # The valid one is migrated
        n = sqlite3.connect(str(db_path)).execute(
            "SELECT COUNT(*) FROM message_parts"
        ).fetchone()[0]
        assert n == 1
