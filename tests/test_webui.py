"""Tests for WebUI — routes + templates + HTMX"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.app import create_app
from strategy_research.webui.routes import router as webui_router


@pytest.fixture
def client(tmp_path):
    """创建带 webui 的测试客户端。"""
    app = create_app(workspace_path=tmp_path)
    app.include_router(webui_router, tags=["webui"])
    return TestClient(app)


@pytest.fixture
def client_with_data(tmp_path):
    """带数据的测试客户端。"""
    # Create strategies with runs
    for name in ["momentum", "value"]:
        strat_dir = tmp_path / "strategies" / name
        run_dir = strat_dir / "runs" / "run_0001"
        run_dir.mkdir(parents=True)
        (strat_dir / "strategy.py").write_text("# placeholder")
        metrics = {"sharpe": 0.5 if name == "momentum" else 0.3, "max_dd": -0.1}
        (run_dir / "metrics.json").write_text(json.dumps(metrics))

    app = create_app(workspace_path=tmp_path)
    app.include_router(webui_router, tags=["webui"])
    return TestClient(app)


class TestDashboard:
    def test_dashboard_empty(self, client):
        resp = client.get("/webui/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text
        assert "Strategy Research" in resp.text

    def test_dashboard_with_data(self, client_with_data):
        resp = client_with_data.get("/webui/")
        assert resp.status_code == 200
        assert "momentum" in resp.text or "value" in resp.text


class TestGoalsPage:
    def test_goals_list(self, client):
        resp = client.get("/webui/goals")
        assert resp.status_code == 200
        assert "Goals" in resp.text


class TestHypothesesPage:
    def test_hypotheses_list(self, client):
        resp = client.get("/webui/hypotheses")
        assert resp.status_code == 200
        assert "Hypotheses" in resp.text


class TestRunsPage:
    def test_runs_list_empty(self, client):
        resp = client.get("/webui/runs")
        assert resp.status_code == 200
        assert "Runs" in resp.text

    def test_runs_list_with_data(self, client_with_data):
        resp = client_with_data.get("/webui/runs")
        assert resp.status_code == 200
        assert "momentum" in resp.text


class TestMemoryPage:
    def test_memory_search_page(self, client):
        resp = client.get("/webui/memory")
        assert resp.status_code == 200
        assert "Memory" in resp.text

    def test_memory_search_htmx(self, client):
        resp = client.get("/webui/memory/search?q=test")
        assert resp.status_code == 200
        # Returns HTML fragment
        assert "No results" in resp.text or "<table" in resp.text


class TestCLI:
    def test_webui_serve_help(self):
        import subprocess
        result = subprocess.run(
            ["python3", "-m", "strategy_research.cli", "webui", "serve", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--host" in result.stdout
        assert "--port" in result.stdout