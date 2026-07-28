"""Tests for auth router and SSE event buffer."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.api.app import create_app
    app = create_app()
    return TestClient(app)


class TestAuth:
    def test_register(self, client):
        res = client.post("/api/auth/register", json={
            "username": "testuser",
            "display_name": "Test User",
            "password": "pass123",
        })
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["user"]["username"] == "testuser"
        assert data["user"]["display_name"] == "Test User"

    def test_register_duplicate(self, client):
        client.post("/api/auth/register", json={
            "username": "dup_user",
            "password": "pass",
        })
        res = client.post("/api/auth/register", json={
            "username": "dup_user",
            "password": "pass",
        })
        assert res.status_code == 409

    def test_login_success(self, client):
        client.post("/api/auth/register", json={
            "username": "login_user",
            "password": "pass123",
        })
        res = client.post("/api/auth/login", json={
            "username": "login_user",
            "password": "pass123",
        })
        assert res.status_code == 200
        assert "access_token" in res.json()

    def test_login_wrong_password(self, client):
        client.post("/api/auth/register", json={
            "username": "wrong_pw_user",
            "password": "pass123",
        })
        res = client.post("/api/auth/login", json={
            "username": "wrong_pw_user",
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

        buf.unregister_session("sess1")


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
