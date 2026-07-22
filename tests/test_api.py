"""Tests for API — FastAPI app + routers + CLI"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.app import create_app


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def client():
    """创建测试客户端。"""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def client_with_goal(tmp_path):
    """带 goal DB 的测试客户端。"""
    db_path = str(tmp_path / "goals.db")
    app = create_app(goal_db_path=db_path)
    return TestClient(app)


@pytest.fixture
def client_with_hypothesis(tmp_path):
    """带 hypothesis 文件的测试客户端。"""
    hyp_path = str(tmp_path / "hypotheses.json")
    app = create_app(hypotheses_path=hyp_path)
    return TestClient(app)


# ============================================================
# app root + health
# ============================================================


class TestAppRoot:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "strategy-research-api"
        assert "docs" in data

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ============================================================
# goal router
# ============================================================


class TestGoalRouter:
    def test_goal_start(self, client_with_goal):
        resp = client_with_goal.post("/api/goal/start", json={
            "session_id": "test-session",
            "objective": "Investigate momentum",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "goal_id" in data

    def test_goal_status_no_goal(self, client_with_goal):
        resp = client_with_goal.get("/api/goal/status?session_id=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_goal"

    def test_goal_list_empty(self, client_with_goal):
        resp = client_with_goal.get("/api/goal/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["goals"], list)


# ============================================================
# hypothesis router
# ============================================================


class TestHypothesisRouter:
    def test_hypothesis_create(self, client_with_hypothesis):
        resp = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "Momentum thesis",
            "thesis": "20-day winners continue",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "hypothesis_id" in data

    def test_hypothesis_list_empty(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["hypotheses"], list)

    def test_hypothesis_search(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/search?q=momentum")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["query"] == "momentum"

    def test_hypothesis_get_not_found(self, client_with_hypothesis):
        resp = client_with_hypothesis.get("/api/hypothesis/nonexistent")
        assert resp.status_code == 404


# ============================================================
# validation router
# ============================================================


class TestValidationRouter:
    def test_validate_run_not_found(self, client):
        resp = client.post("/api/validate/run", json={
            "run_dir": "/nonexistent/path",
        })
        assert resp.status_code == 404


# ============================================================
# session router
# ============================================================


class TestSessionRouter:
    def test_session_list(self, client):
        resp = client.get("/api/session/list?workspace_path=/tmp")
        assert resp.status_code == 200


# ============================================================
# memory router
# ============================================================


class TestMemoryRouter:
    def test_memory_search(self, client):
        resp = client.get("/api/memory/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ============================================================
# run router
# ============================================================


class TestRunRouter:
    def test_run_list_no_workspace(self, client):
        resp = client.get("/api/run/list?workspace_path=/nonexistent&strategy_name=test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []

    def test_run_status_not_found(self, client):
        resp = client.get("/api/run/status?workspace_path=/tmp&strategy_name=test&run_name=run_0001")
        assert resp.status_code == 404


# ============================================================
# CLI: api serve
# ============================================================


class TestAPIServeCLI:
    def test_api_serve_help(self):
        import subprocess
        result = subprocess.run(
            ["python3", "-m", "strategy_research.cli", "api", "serve", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--host" in result.stdout
        assert "--port" in result.stdout
