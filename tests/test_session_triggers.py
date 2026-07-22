import pytest
import time
from pathlib import Path
from strategy_research.core.session import SessionDB


class TestTriggerSync:
    def test_insert_trigger(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "hello world")
        results = db.search_messages("hello")
        assert len(results) == 1

    def test_delete_trigger(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "hello world")
        # Verify message exists
        results = db.search_messages("hello")
        assert len(results) == 1
        # Delete session (cascades to messages)
        db.delete_session("s1")
        # Verify FTS5 index is also cleaned
        results = db.search_messages("hello")
        assert len(results) == 0

    def test_update_trigger(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "original content")
        # Search for original
        results = db.search_messages("original")
        assert len(results) == 1
        # Update message
        with sqlite3.connect(str(db._db_path)) as conn:
            conn.execute(
                "UPDATE messages SET content = ? WHERE session_id = ?",
                ("updated content", "s1"),
            )
        # Search for updated content
        results = db.search_messages("updated")
        assert len(results) == 1
        # Old content should not be found
        results = db.search_messages("original")
        assert len(results) == 0


class TestTriggerPerformance:
    def test_bulk_insert(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        start = time.time()
        for i in range(1000):
            db.add_message("s1", "user", f"Message number {i} with some content")
        elapsed = time.time() - start
        print(f"\nBulk insert (1000 messages): {elapsed:.3f}s")
        assert elapsed < 2.0  # Should be under 2 seconds

    def test_search_performance(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        # Insert test data
        for i in range(100):
            db.add_message("s1", "user", f"Test message {i} with keyword momentum")
        # Search performance
        start = time.time()
        for _ in range(100):
            db.search_messages("momentum")
        elapsed = time.time() - start
        print(f"\nSearch performance (100 searches): {elapsed:.3f}s")
        assert elapsed < 0.1  # Should be under 100ms

    def test_mixed_operations(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        start = time.time()
        # Mix of operations
        for i in range(100):
            db.add_message("s1", "user", f"Message {i}")
            if i % 10 == 0:
                db.search_messages(f"Message {i}")
        elapsed = time.time() - start
        print(f"\nMixed operations (100 cycles): {elapsed:.3f}s")
        assert elapsed < 2.0  # Should be under 2 seconds


# Need to import sqlite3 for update test
import sqlite3
