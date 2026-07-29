"""Tests for auth router and SSE event buffer."""

import pytest
import time
import uuid
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.api.app import create_app
    app = create_app()
    return TestClient(app)


class TestAuth:
    def test_register_disabled(self, client):
        """Registration is disabled — returns 403."""
        res = client.post("/api/auth/register", json={
            "username": "testuser",
            "display_name": "Test User",
            "password": "pass123",
        })
        assert res.status_code == 403

    def test_register_disabled_always(self, client):
        """Even duplicate usernames return 403 (register disabled)."""
        res = client.post("/api/auth/register", json={
            "username": "dup_user",
            "password": "pass",
        })
        assert res.status_code == 403

    def test_login_success(self, client):
        """Default admin/admin is seeded on first startup."""
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin",
        })
        assert res.status_code == 200
        assert "access_token" in res.json()
        assert res.json()["user"]["username"] == "admin"

    def test_login_wrong_password(self, client):
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong",
        })
        assert res.status_code == 401

    def test_login_nonexistent(self, client):
        res = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "pass",
        })
        assert res.status_code == 401


class TestSSEBuffer:
    def test_push_and_replay(self):
        from strategy_research.api.sse_buffer import SSEEventBuffer
        buf = SSEEventBuffer(max_events=100, ttl_seconds=300)

        eid1 = buf.push("text_delta", '{"delta":"hello"}', "sess1")
        eid2 = buf.push("text_delta", '{"delta":" world"}', "sess1")
        eid3 = buf.push("text_delta", '{"delta":"!"}', "sess1")

        # Replay from eid1 → should get eid2 and eid3
        events = buf.replay_from(eid1, "sess1")
        assert len(events) == 2
        assert events[0].id == eid2
        assert events[1].id == eid3

    def test_replay_from_unknown_id(self):
        from strategy_research.api.sse_buffer import SSEEventBuffer
        buf = SSEEventBuffer()

        buf.push("text_delta", '{"delta":"a"}', "sess1")
        buf.push("text_delta", '{"delta":"b"}', "sess1")

        # Unknown ID → returns empty (no events found after it)
        events = buf.replay_from("evt_999", "sess1")
        assert len(events) == 0

    def test_session_isolation(self):
        from strategy_research.api.sse_buffer import SSEEventBuffer
        buf = SSEEventBuffer()

        buf.push("text_delta", '{"delta":"a"}', "sess1")
        buf.push("text_delta", '{"delta":"b"}', "sess2")

        # Replay sess1 should not include sess2 events
        events = buf.replay_from("evt_1", "sess1")
        assert all(e.session_id == "sess1" for e in events)

    def test_max_events(self):
        from strategy_research.api.sse_buffer import SSEEventBuffer
        buf = SSEEventBuffer(max_events=5)

        for i in range(10):
            buf.push("text_delta", f'{{"delta":"{i}"}}', "sess1")

        # Only last 5 events should be in buffer
        with buf._lock:
            assert len(buf._buffer) == 5

    def test_async_notification(self):
        import asyncio
        from strategy_research.api.sse_buffer import SSEEventBuffer
        buf = SSEEventBuffer()

        evt = buf.register_session("sess1")
        assert not evt.is_set()

        buf.push("text_delta", '{"delta":"hello"}', "sess1")
        assert evt.is_set()

        buf.unregister_session("sess1", evt)


class TestWebSession:
    def test_create_session(self, client):
        res = client.post("/api/chat/session", json={"title": "Test Session"})
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Test Session"
        assert "id" in data

    def test_list_sessions(self, client):
        client.post("/api/chat/session", json={"title": "S1"})
        client.post("/api/chat/session", json={"title": "S2"})
        res = client.get("/api/chat/session")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        assert len(sessions) >= 2

    def test_delete_session(self, client):
        res = client.post("/api/chat/session", json={"title": "To Delete"})
        session_id = res.json()["id"]
        res = client.delete(f"/api/chat/session/{session_id}")
        assert res.status_code == 200


class TestChatAPI:
    def test_send_async(self, client):
        res = client.post("/api/chat/send_async", json={
            "session_id": "test-session",
            "content": "Hello",
        })
        assert res.status_code == 200
        data = res.json()
        assert "message_id" in data
        assert "event_id" in data
        assert data["status"] == "processing"

    def test_send_sync(self, client):
        res = client.post("/api/chat/send", json={
            "session_id": "test-session",
            "content": "Hello",
        })
        # 200 if LLM is configured, 503 if not
        assert res.status_code in (200, 503)


# ─────────────────────────────────────────────────────────────────────────────
# History Sessions + Messages + FTS5 Search
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionMessages:
    def test_create_session_with_new_fields(self, client):
        """New sessions include starred/tags/message_count/archived defaults."""
        res = client.post("/api/chat/session", json={"title": "新会话"})
        assert res.status_code == 200
        data = res.json()
        assert data["starred"] is False
        assert data["tags"] == []
        assert data["message_count"] == 0
        assert data["archived"] is False

    def test_patch_session_title(self, client):
        res = client.post("/api/chat/session", json={"title": "Old"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"title": "New"})
        assert res.status_code == 200
        assert res.json()["title"] == "New"

    def test_patch_session_starred(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"starred": True})
        assert res.status_code == 200
        assert res.json()["starred"] is True

    def test_patch_session_tags(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"tags": ["alpha", "策略"]})
        assert res.status_code == 200
        assert res.json()["tags"] == ["alpha", "策略"]

    def test_patch_session_archived(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"archived": True})
        assert res.status_code == 200
        assert res.json()["archived"] is True

    def test_patch_multiple_fields(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={
            "title": "T2",
            "starred": True,
            "tags": ["x"],
            "archived": True,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "T2"
        assert body["starred"] is True
        assert body["tags"] == ["x"]
        assert body["archived"] is True

    def test_patch_404(self, client):
        res = client.patch(f"/api/chat/session/nonexistent-{uuid.uuid4()}", json={"title": "X"})
        assert res.status_code == 404


class TestMessages:
    def _create_session(self, client):
        res = client.post("/api/chat/session", json={"title": "测试"})
        return res.json()["id"]

    def test_list_messages_empty(self, client):
        sid = self._create_session(client)
        res = client.get(f"/api/chat/session/{sid}/messages")
        assert res.status_code == 200
        body = res.json()
        assert body["messages"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    def test_list_messages_404(self, client):
        res = client.get(f"/api/chat/session/nonexistent-{uuid.uuid4()}/messages")
        assert res.status_code == 404

    def test_persist_message_via_helper(self, client):
        from strategy_research.api.routers.web_session import (
            persist_message, _get_db,
        )
        sid = self._create_session(client)
        msg_id = persist_message(
            session_id=sid,
            role="user",
            content="测试消息",
        )
        # Verify it's in DB
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        assert row is not None
        assert row["role"] == "user"
        assert row["content"] == "测试消息"
        # Verify session counter incremented
        sess = conn.execute(
            "SELECT message_count FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert sess["message_count"] == 1

    def test_persist_message_with_parts(self, client):
        from strategy_research.api.routers.web_session import persist_message
        sid = self._create_session(client)
        parts = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_call", "id": "t1", "name": "fetch", "arguments": "{}"},
        ]
        msg_id = persist_message(
            session_id=sid, role="assistant",
            content="Hello", parts=parts,
        )
        res = client.get(f"/api/chat/session/{sid}/messages")
        msgs = res.json()["messages"]
        assert len(msgs) == 1
        m = msgs[0]
        assert m["role"] == "assistant"
        assert m["content"] == "Hello"
        assert m["parts"] == parts

    def test_delete_session_cascades_messages(self, client):
        from strategy_research.api.routers.web_session import persist_message
        sid = self._create_session(client)
        persist_message(session_id=sid, role="user", content="msg1")
        persist_message(session_id=sid, role="assistant", content="reply")
        # Verify 2 messages exist
        res = client.get(f"/api/chat/session/{sid}/messages")
        assert res.json()["total"] == 2
        # Delete session
        client.delete(f"/api/chat/session/{sid}")
        # Messages should be gone
        from strategy_research.api.routers.web_session import _get_db
        conn = _get_db()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()["c"]
        assert count == 0

    def test_messages_pagination(self, client):
        from strategy_research.api.routers.web_session import persist_message
        sid = self._create_session(client)
        for i in range(5):
            persist_message(
                session_id=sid, role="user",
                content=f"msg-{i}",
                created_at=time.time() + i,
            )
        res = client.get(f"/api/chat/session/{sid}/messages?limit=2")
        body = res.json()
        assert len(body["messages"]) == 2
        assert body["has_more"] is True
        assert body["total"] == 5


class TestAutoTitle:
    def test_auto_title_from_first_message(self, client):
        from strategy_research.api.routers.web_session import (
            persist_message, auto_title_session, _get_db,
        )
        res = client.post("/api/chat/session", json={"title": "新会话"})
        sid = res.json()["id"]
        new_title = auto_title_session(sid, "帮我设计一个 alpha 策略")
        assert new_title is not None
        assert new_title.startswith("帮我设计一个")
        conn = _get_db()
        row = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert row["title"].startswith("帮我设计一个")
        assert row["title"].endswith("…") or len("帮我设计一个 alpha 策略") <= 30

    def test_auto_title_skips_non_default(self, client):
        from strategy_research.api.routers.web_session import (
            auto_title_session, _get_db,
        )
        res = client.post("/api/chat/session", json={"title": "Custom"})
        sid = res.json()["id"]
        new_title = auto_title_session(sid, "any content")
        assert new_title is None

    def test_auto_title_truncates_long(self, client):
        from strategy_research.api.routers.web_session import auto_title_session
        res = client.post("/api/chat/session", json={"title": "新会话"})
        sid = res.json()["id"]
        long = "x" * 100
        auto_title_session(sid, long)
        from strategy_research.api.routers.web_session import _get_db
        conn = _get_db()
        row = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        # Should be 29 chars + "…" = 30 chars total (max_len=30)
        assert len(row["title"]) <= 31


class TestFTSSearch:
    def test_search_returns_hits(self, client):
        from strategy_research.api.routers.web_session import persist_message
        res = client.post("/api/chat/session", json={"title": "搜索测试"})
        sid = res.json()["id"]
        persist_message(
            session_id=sid, role="user",
            content="请帮我设计一个 alpha 量化策略",
        )
        persist_message(
            session_id=sid, role="assistant",
            content="好的，alpha 策略需要考虑因子选择",
        )
        res = client.post("/api/chat/session/search", json={"query": "alpha"})
        assert res.status_code == 200
        body = res.json()
        assert "hits" in body
        assert len(body["hits"]) >= 1
        for hit in body["hits"]:
            assert "session_id" in hit
            assert "message_id" in hit
            assert "snippet" in hit
            assert "<mark>" in hit["snippet"]

    def test_search_no_results(self, client):
        res = client.post("/api/chat/session/search", json={"query": "完全不存在_xyz123"})
        assert res.status_code == 200
        assert res.json()["hits"] == []

    def test_search_empty_query(self, client):
        res = client.post("/api/chat/session/search", json={"query": ""})
        assert res.status_code == 200
        assert res.json()["hits"] == []

    def test_search_filters_archived(self, client):
        from strategy_research.api.routers.web_session import persist_message
        res = client.post("/api/chat/session", json={"title": "归档"})
        sid = res.json()["id"]
        persist_message(session_id=sid, role="user", content="uniquearchword123")
        # Archive the session
        client.patch(f"/api/chat/session/{sid}", json={"archived": True})
        # Search should not find it
        res = client.post("/api/chat/session/search", json={"query": "uniquearchword123"})
        assert res.status_code == 200
        assert res.json()["hits"] == []
