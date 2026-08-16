"""Tests for the ``/api/admin/metrics`` HTTP endpoint.

Wraps the existing in-memory ``MetricsLogger`` so an operator can read
session-write throughput without shelling into the box. Auth follows
the rest of the admin surface (``X-Admin-Token``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.app import create_app
from strategy_research.core.session.metrics import MetricsLogger


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_ADMIN_TOKEN", "test-admin-secret-metrics")
    # Need SR_SESSIONS_DB for SessionDB construction
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    return TestClient(create_app())


class _FakeDB:
    """Stands in for ``SessionDB`` so both the test and the endpoint read
    the same MetricsLogger instance."""

    def __init__(self, logger: MetricsLogger):
        self.metrics_logger = logger


class TestAdminMetricsAuth:
    def test_requires_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_ADMIN_TOKEN", "secret")
        monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
        # No header → 401
        c = TestClient(create_app())
        r = c.get("/api/admin/metrics")
        assert r.status_code == 401

    def test_disabled_returns_503(self, monkeypatch):
        monkeypatch.delenv("SR_ADMIN_TOKEN", raising=False)
        c = TestClient(create_app())
        r = c.get("/api/admin/metrics")
        assert r.status_code == 503


class TestAdminMetricsPayload:
    def test_empty_stats(self, admin_client):
        r = admin_client.get(
            "/api/admin/metrics",
            headers={"X-Admin-Token": "test-admin-secret-metrics"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["stats"]["total_writes"] == 0
        assert body["recent"] == []

    def test_with_records(self, admin_client, monkeypatch):
        # Inject a few synthetic records straight into the SessionDB
        # backing MetricsLogger.
        logger = MetricsLogger()
        monkeypatch.setattr(
            "strategy_research.core.session.SessionDB",
            lambda: _FakeDB(logger),
        )
        for i in range(3):
            logger.record_write(
                count=10, duration=0.5, success=(i < 2),
                error=(None if i < 2 else "boom"),
            )

        r = admin_client.get(
            "/api/admin/metrics",
            headers={"X-Admin-Token": "test-admin-secret-metrics"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["stats"]["total_writes"] == 3
        assert body["stats"]["total_messages"] == 30
        # 2/3 success
        assert abs(body["stats"]["success_rate"] - 2 / 3) < 0.01
        assert len(body["recent"]) == 3

    def test_recent_param_caps(self, admin_client, monkeypatch):
        logger = MetricsLogger()
        monkeypatch.setattr(
            "strategy_research.core.session.SessionDB",
            lambda: _FakeDB(logger),
        )
        for _ in range(50):
            logger.record_write(count=1, duration=0.1, success=True)

        r = admin_client.get(
            "/api/admin/metrics?recent=5",
            headers={"X-Admin-Token": "test-admin-secret-metrics"},
        )
        body = r.json()
        assert len(body["recent"]) == 5

    def test_recent_param_upper_bound(self, admin_client):
        # recent > 200 must be rejected (Query ge=1, le=200)
        r = admin_client.get(
            "/api/admin/metrics?recent=10000",
            headers={"X-Admin-Token": "test-admin-secret-metrics"},
        )
        assert r.status_code == 422
