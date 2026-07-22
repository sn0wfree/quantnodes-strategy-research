import pytest
import time
from pathlib import Path
from strategy_research.core.session import Session, SessionDB, SessionManager, SessionMessage


class TestSessionModels:
    def test_session_message_frozen(self):
        msg = SessionMessage(role="user", content="hello", timestamp=1.0)
        with pytest.raises(AttributeError):
            msg.role = "assistant"

    def test_session_frozen(self):
        session = Session(id="s1", created_at=1.0, updated_at=1.0)
        with pytest.raises(AttributeError):
            session.id = "s2"

    def test_session_with_metadata(self):
        session = Session(id="s1", created_at=1.0, updated_at=1.0, metadata={"key": "value"})
        assert session.metadata["key"] == "value"

    def test_session_with_messages(self):
        msg = SessionMessage(role="user", content="hello", timestamp=1.0)
        session = Session(id="s1", created_at=1.0, updated_at=1.0, messages=(msg,))
        assert len(session.messages) == 1


class TestSessionDBInit:
    def test_init_creates_db(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        assert (tmp_path / "test.db").exists()

    def test_init_default_path(self):
        db = SessionDB()
        assert db._db_path.name == "sessions.db"


class TestSessionDBCreate:
    def test_create_session(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        session = db.create_session("s1", workspace="/tmp/ws")
        assert session.id == "s1"
        assert session.workspace == "/tmp/ws"

    def test_create_session_with_metadata(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        session = db.create_session("s1", metadata={"key": "value"})
        assert session.metadata["key"] == "value"


class TestSessionDBGet:
    def test_get_session(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        session = db.get_session("s1")
        assert session is not None
        assert session.id == "s1"

    def test_get_session_missing(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        session = db.get_session("nonexistent")
        assert session is None


class TestSessionDBList:
    def test_list_sessions_empty(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        sessions = db.list_sessions()
        assert sessions == []

    def test_list_sessions_multiple(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.create_session("s2")
        db.create_session("s3")
        sessions = db.list_sessions()
        assert len(sessions) == 3

    def test_list_sessions_by_workspace(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1", workspace="/ws1")
        db.create_session("s2", workspace="/ws2")
        db.create_session("s3", workspace="/ws1")
        sessions = db.list_sessions(workspace="/ws1")
        assert len(sessions) == 2

    def test_list_sessions_limit(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        for i in range(10):
            db.create_session(f"s{i}")
        sessions = db.list_sessions(limit=5)
        assert len(sessions) == 5


class TestSessionDBMessages:
    def test_add_message(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        msg = db.add_message("s1", "user", "hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_add_message_with_metadata(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        msg = db.add_message("s1", "user", "hello", metadata={"key": "value"})
        assert msg.metadata["key"] == "value"

    def test_get_messages_empty(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        messages = db.get_messages("s1")
        assert messages == []

    def test_get_messages_multiple(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "hello")
        db.add_message("s1", "assistant", "hi")
        db.add_message("s1", "user", "bye")
        messages = db.get_messages("s1")
        assert len(messages) == 3
        assert messages[0].role == "user"
        assert messages[2].role == "user"

    def test_get_messages_limit(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        for i in range(10):
            db.add_message("s1", "user", f"msg {i}")
        messages = db.get_messages("s1", limit=3)
        assert len(messages) == 3


class TestSessionDBDelete:
    def test_delete_session(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "hello")
        result = db.delete_session("s1")
        assert result is True
        assert db.get_session("s1") is None

    def test_delete_session_missing(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        result = db.delete_session("nonexistent")
        assert result is False


class TestSessionDBSearch:
    def test_search_messages(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "momentum strategy")
        db.add_message("s1", "assistant", "value factor")
        results = db.search_messages("momentum")
        assert len(results) >= 1

    def test_search_empty_query(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        results = db.search_messages("")
        assert results == []

    def test_search_no_results(self, tmp_path):
        db = SessionDB(tmp_path / "test.db")
        db.create_session("s1")
        db.add_message("s1", "user", "hello")
        results = db.search_messages("nonexistent")
        assert results == []


class TestSessionManager:
    def test_create_session(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        session = manager.create_session("s1", workspace="/tmp/ws")
        assert session.id == "s1"

    def test_get_session(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        session = manager.get_session("s1")
        assert session is not None

    def test_list_sessions(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        manager.create_session("s2")
        sessions = manager.list_sessions()
        assert len(sessions) == 2

    def test_add_message(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        msg = manager.add_message("s1", "user", "hello")
        assert msg.role == "user"

    def test_get_messages(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        manager.add_message("s1", "user", "hello")
        messages = manager.get_messages("s1")
        assert len(messages) == 1

    def test_search_messages(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        manager.add_message("s1", "user", "momentum strategy")
        results = manager.search_messages("momentum")
        assert len(results) >= 1

    def test_delete_session(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        result = manager.delete_session("s1")
        assert result is True

    def test_archive_session(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        manager.add_message("s1", "user", "hello")
        manager.add_message("s1", "assistant", "hi")
        result = manager.archive_session("s1")
        assert result is not None
        assert "Session: s1" in result

    def test_archive_session_empty(self, tmp_path):
        manager = SessionManager(SessionDB(tmp_path / "test.db"))
        manager.create_session("s1")
        result = manager.archive_session("s1")
        assert result is None
