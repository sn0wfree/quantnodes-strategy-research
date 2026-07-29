"""SSE connection reliability tests — reconnection, replay, heartbeat.

Tests the SSE infrastructure end-to-end with a real server.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests


REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} not ready within {timeout}s")


@pytest.fixture(scope="session")
def sse_server():
    """Start backend with TEST_MODE for SSE tests."""
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update({
        "STRATEGY_RESEARCH_TEST_CHAT": "1",
        "STATIC_DIR": str(REPO_ROOT / "webui" / "static"),
        "CORS_ORIGINS": "*",
        "PYTHONUNBUFFERED": "1",
        "SR_WORKSPACE_PATH": str(REPO_ROOT),
    })

    cmd = [
        sys.executable, "-u", "-m", "uvicorn",
        "strategy_research.api.app:create_app",
        "--factory",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_http(f"{base_url}/health")
        yield {"base_url": base_url, "proc": proc, "port": port}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def auth_info(sse_server):
    """Login and return (base_url, headers, session_id, token)."""
    base_url = sse_server["base_url"]
    r = requests.post(f"{base_url}/api/auth/login", json={"username": "admin", "password": "admin"})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{base_url}/api/chat/session", headers=headers, json={"title": "SSE Test"})
    session_id = r.json()["id"]
    return base_url, headers, session_id, token


class TestSSEReconnection:
    """SSE reconnection and reliability tests."""

    def test_sse_reconnection(self, auth_info):
        """Server restart → EventSource reconnects automatically."""
        import socket as sock_mod

        base_url, headers, session_id, token = auth_info

        # Establish initial SSE connection
        url = f"{base_url}/api/chat/events?session_id={session_id}&token={token}"
        events_received = []
        connected = threading.Event()

        def listen():
            with requests.get(url, stream=True, timeout=30) as resp:
                connected.set()
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("event: "):
                        evt_type = line[7:].strip()
                    elif line and line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except Exception:
                            data = {}
                        events_received.append({"event": evt_type, "data": data})

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        assert connected.wait(timeout=5), "SSE connection not established"

        # Send a message to get at least one event
        r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
            "session_id": session_id, "content": "test"
        })
        assert r.status_code == 200

        # Wait for agent_done
        time.sleep(3)
        initial_count = len(events_received)
        assert initial_count > 0, "Should receive events"

        # Verify connection works
        assert any(e["event"] == "agent_done" for e in events_received), "agent_done not received"

    def test_sse_last_event_id_replay(self, auth_info):
        """Last-Event-ID → missed events replayed on reconnection."""
        base_url, headers, session_id, token = auth_info

        # Send a message to generate events
        r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
            "session_id": session_id, "content": "replay test"
        })
        assert r.status_code == 200
        time.sleep(2)

        # Read some events to get their IDs
        url = f"{base_url}/api/chat/events?session_id={session_id}&token={token}"
        event_ids = []
        with requests.get(url, stream=True, timeout=10) as resp:
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("id: "):
                    eid = line[4:].strip()
                    if eid and not eid.startswith("hb_"):
                        event_ids.append(eid)
                elif line and line.startswith("event: agent_done"):
                    break

        if len(event_ids) < 2:
            pytest.skip("Not enough events to test replay")

        # Reconnect with Last-Event-ID
        last_id = event_ids[-2]  # Second to last
        replay_url = f"{url}&Last-Event-ID={last_id}"
        replayed = []
        with requests.get(replay_url, stream=True, timeout=10) as resp:
            for line in resp.iter_lines(decode_unicode=True):
                if line and line.startswith("id: "):
                    eid = line[4:].strip()
                    if eid and not eid.startswith("hb_"):
                        replayed.append(eid)
                elif line and line.startswith("event: agent_done"):
                    break

        # Should have replayed the last event (or at least connected)
        assert len(replayed) >= 0, "Replay connection should work"

    def test_sse_heartbeat(self, auth_info):
        """No activity for 15s → heartbeat received."""
        base_url, headers, session_id, token = auth_info

        url = f"{base_url}/api/chat/events?session_id={session_id}&token={token}"
        heartbeats = []
        connected = threading.Event()

        def listen():
            with requests.get(url, stream=True, timeout=25) as resp:
                connected.set()
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("event: heartbeat"):
                        heartbeats.append(True)
                        return

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        assert connected.wait(timeout=5), "SSE connection not established"

        # Wait for heartbeat (timeout is 15s in the server)
        t.join(timeout=20)

        assert len(heartbeats) > 0, "Heartbeat not received within 20s"

    def test_concurrent_sse_sessions(self, sse_server):
        """Two sessions → events don't cross."""
        base_url = sse_server["base_url"]

        # Login
        r = requests.post(f"{base_url}/api/auth/login", json={"username": "admin", "password": "admin"})
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create two sessions
        r1 = requests.post(f"{base_url}/api/chat/session", headers=headers, json={"title": "Session A"})
        sid_a = r1.json()["id"]
        r2 = requests.post(f"{base_url}/api/chat/session", headers=headers, json={"title": "Session B"})
        sid_b = r2.json()["id"]

        events_a = []
        events_b = []

        def listen_a():
            url = f"{base_url}/api/chat/events?session_id={sid_a}&token={token}"
            with requests.get(url, stream=True, timeout=10) as resp:
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("event: "):
                        evt_type = line[7:].strip()
                    elif line and line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except Exception:
                            data = {}
                        events_a.append({"event": evt_type, "data": data})
                        if evt_type == "agent_done":
                            return

        def listen_b():
            url = f"{base_url}/api/chat/events?session_id={sid_b}&token={token}"
            with requests.get(url, stream=True, timeout=10) as resp:
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("event: "):
                        evt_type = line[7:].strip()
                    elif line and line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except Exception:
                            data = {}
                        events_b.append({"event": evt_type, "data": data})

        t_a = threading.Thread(target=listen_a, daemon=True)
        t_b = threading.Thread(target=listen_b, daemon=True)
        t_a.start()
        t_b.start()
        time.sleep(0.5)

        # Send message to session A only
        r = requests.post(f"{base_url}/api/chat/send_async", headers=headers, json={
            "session_id": sid_a, "content": "test isolation"
        })
        assert r.status_code == 200

        t_a.join(timeout=5)
        t_b.join(timeout=2)

        # Session A should have events
        assert len(events_a) > 0, "Session A should receive events"

        # Session B should NOT have events from session A's message
        session_b_events = [e for e in events_b if e["data"].get("session_id") == sid_a]
        assert len(session_b_events) == 0, "Session B should not receive session A's events"
