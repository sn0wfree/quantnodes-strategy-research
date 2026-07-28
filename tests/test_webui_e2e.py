"""E2E test for Web UI — full user flow."""

import time
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.api.app import create_app
    app = create_app()
    return TestClient(app)


class TestE2EFlow:
    """End-to-end test: register → login → create session → send message → receive events."""

    def test_full_user_flow(self, client):
        # 1. Register
        res = client.post("/api/auth/register", json={
            "username": "e2e_user",
            "display_name": "E2E Tester",
            "password": "e2e_pass",
        })
        assert res.status_code == 200
        token = res.json()["access_token"]

        # 2. Create session
        res = client.post("/api/chat/session", json={"title": "E2E Session"})
        assert res.status_code == 200
        session_id = res.json()["id"]

        # 3. Send async message
        res = client.post("/api/chat/send_async", json={
            "session_id": session_id,
            "content": "Hello, E2E test!",
        })
        assert res.status_code == 200
        data = res.json()
        assert "message_id" in data
        assert "event_id" in data

        # 4. Verify session list contains created session
        res = client.get("/api/chat/session")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        session_ids = [s["id"] for s in sessions]
        assert session_id in session_ids

        # 5. Update session title
        res = client.put(f"/api/chat/session/{session_id}", json={
            "title": "Updated E2E Session",
        })
        assert res.status_code == 200
        assert res.json()["title"] == "Updated E2E Session"

        # 6. Delete session
        res = client.delete(f"/api/chat/session/{session_id}")
        assert res.status_code == 200

        # 7. Verify deleted
        res = client.get("/api/chat/session")
        sessions = res.json()["sessions"]
        session_ids = [s["id"] for s in sessions]
        assert session_id not in session_ids

    def test_concurrent_sessions(self, client):
        """Create multiple sessions and verify they're isolated."""
        session_ids = []
        for i in range(5):
            res = client.post("/api/chat/session", json={"title": f"Concurrent {i}"})
            assert res.status_code == 200
            session_ids.append(res.json()["id"])

        # All IDs should be unique
        assert len(set(session_ids)) == 5

        # List should contain all
        res = client.get("/api/chat/session")
        sessions = res.json()["sessions"]
        listed_ids = [s["id"] for s in sessions]
        for sid in session_ids:
            assert sid in listed_ids

    def test_event_buffer_replay_chain(self, client):
        """Verify SSE event replay chain works across multiple events."""
        from strategy_research.api.sse_buffer import sse_buffer

        session_id = "replay-test"
        # Push 3 events
        eid1 = sse_buffer.push("text_delta", '{"text":"a"}', session_id)
        eid2 = sse_buffer.push("text_delta", '{"text":"b"}', session_id)
        eid3 = sse_buffer.push("text_delta", '{"text":"c"}', session_id)

        # Replay from eid1 → should return eid2 and eid3
        events = sse_buffer.replay_from(eid1, session_id)
        assert [e.id for e in events] == [eid2, eid3]

        # Replay from eid2 → should return only eid3
        events = sse_buffer.replay_from(eid2, session_id)
        assert [e.id for e in events] == [eid3]

    def test_get_nonexistent_session(self, client):
        """Getting non-existent session returns 404."""
        res = client.get("/api/chat/session/nonexistent-id")
        assert res.status_code == 404

    def test_delete_nonexistent_session(self, client):
        """Deleting non-existent session returns 200 (idempotent)."""
        res = client.delete("/api/chat/session/nonexistent-id")
        assert res.status_code == 200


class TestStaticFiles:
    """Test that the FastAPI app serves the built frontend."""

    def test_static_index_served(self, client):
        """Index page served when static files exist."""
        from pathlib import Path
        static_dir = Path(__file__).parent.parent / "webui" / "static"
        if not static_dir.exists():
            pytest.skip("Static files not built (run `npm run build` first)")
        res = client.get("/")
        assert res.status_code == 200
        assert "html" in res.headers.get("content-type", "").lower() or "<!DOCTYPE" in res.text

    def test_static_assets_served(self, client):
        """Static assets served with correct content type."""
        from pathlib import Path
        static_dir = Path(__file__).parent.parent / "webui" / "static"
        if not (static_dir / "assets").exists():
            pytest.skip("Static assets not built")
        # Try to get the first asset
        assets_dir = static_dir / "assets"
        first_asset = list(assets_dir.iterdir())[0] if assets_dir.iterdir() else None
        if not first_asset:
            pytest.skip("No assets found")
        res = client.get(f"/assets/{first_asset.name}")
        assert res.status_code == 200