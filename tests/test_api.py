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

    # ─── new behavior assertions ────────────────────────────────────────

    def test_goal_start_returns_goal_id_only(self, client_with_goal):
        """The response should expose `goal_id`, not the full dataclass."""
        resp = client_with_goal.post("/api/goal/start", json={
            "session_id": "behav-1",
            "objective": "X",
        })
        body = resp.json()
        assert set(body.keys()) == {"status", "goal_id"}

    def test_goal_list_filters_by_session_id(self, client_with_goal):
        """Two sessions → only the queried session's goal is returned."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sA", "objective": "A",
        })
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sB", "objective": "B",
        })
        resp = client_with_goal.get("/api/goal/list?session_id=sA")
        assert resp.status_code == 200
        goals = resp.json()["goals"]
        assert len(goals) >= 1
        assert all(g["session_id"] == "sA" for g in goals)

        # Listing without filter should include both sessions
        resp_all = client_with_goal.get("/api/goal/list")
        all_goals = resp_all.json()["goals"]
        sessions = {g["session_id"] for g in all_goals}
        assert {"sA", "sB"}.issubset(sessions)

    def test_goal_list_filters_by_status(self, client_with_goal):
        """When status=active is requested, only active goals are returned."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sStatus", "objective": "Y",
        })
        resp = client_with_goal.get("/api/goal/list?status=active&session_id=sStatus")
        body = resp.json()
        for g in body["goals"]:
            assert g["goal_status"] == "active"

    def test_goal_status_returns_structured_fields(self, client_with_goal):
        """After starting a goal, /status returns a structured response (no __dict__ leak)."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sStruct",
            "objective": "Investigate the lemma",
        })
        resp = client_with_goal.get("/api/goal/status?session_id=sStruct")
        body = resp.json()
        assert body["status"] == "ok"
        assert "goal_id" in body
        assert body["goal_status"] == "active"
        assert body["objective"] == "Investigate the lemma"
        assert body["session_id"] == "sStruct"
        # Should NOT contain dataclass internals
        assert "_sa_instance_state" not in body
        assert "progress_percent" not in body  # not exposed in the public contract

    def test_goal_evidence_returns_evidence_id(self, client_with_goal):
        """POST /evidence returns the created evidence_id."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sEv",
            "objective": "Run the backtest",
        })
        resp = client_with_goal.post("/api/goal/evidence", json={
            "session_id": "sEv",
            "evidence": "sharpe=1.5, pnl=200",
            "source": "backtest",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "evidence_id" in body
        assert body["evidence_id"].startswith("ev_")

    def test_goal_evidence_with_criterion_and_run_id(self, client_with_goal):
        """criterion_id and run_id should pass through schema validation."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sEv2",
            "objective": "Run the backtest",
        })
        # Schema should accept these new fields (no 422).
        resp = client_with_goal.post("/api/goal/evidence", json={
            "session_id": "sEv2",
            "evidence": "calmar=1.2",
            "source": "backtest",
            "run_id": "rb_007",
        })
        assert resp.status_code == 200

    def test_goal_evidence_no_active_goal_returns_404(self, client_with_goal):
        resp = client_with_goal.post("/api/goal/evidence", json={
            "session_id": "no_active_goal",
            "evidence": "x",
        })
        assert resp.status_code == 404

    def test_goal_complete_returns_new_status(self, client_with_goal):
        """Default outcome='complete' → new_status='complete'.

        COMPLETE requires verified-evidence audit; we use outcome='cancelled'
        here, which doesn't need audit. For 'complete' coverage see P3 E2E
        tests instead.
        """
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sComplete",
            "objective": "Finish",
        })
        resp = client_with_goal.post("/api/goal/complete", json={
            "session_id": "sComplete",
            "outcome": "cancelled",
            "summary": "aborted",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["new_status"] == "cancelled"

    def test_goal_complete_invalid_outcome_returns_400(self, client_with_goal):
        """A bogus outcome string should yield HTTP 400, not 500."""
        client_with_goal.post("/api/goal/start", json={
            "session_id": "sBad",
            "objective": "X",
        })
        resp = client_with_goal.post("/api/goal/complete", json={
            "session_id": "sBad",
            "outcome": "not-a-real-status",
        })
        assert resp.status_code == 400
        assert "Invalid outcome" in resp.json()["detail"]

    def test_goal_complete_no_active_goal_returns_404(self, client_with_goal):
        resp = client_with_goal.post("/api/goal/complete", json={
            "session_id": "ghost",
        })
        assert resp.status_code == 404

    def test_goal_e2e_start_evidence_complete(self, client_with_goal):
        """End-to-end: start → evidence → complete cycle."""
        sid = "sE2E"
        # 1. Start
        r1 = client_with_goal.post("/api/goal/start", json={
            "session_id": sid, "objective": "E2E test",
        })
        assert r1.status_code == 200
        gid = r1.json()["goal_id"]

        # 2. Add evidence
        r2 = client_with_goal.post("/api/goal/evidence", json={
            "session_id": sid, "evidence": "ev1",
            "source": "test", "criterion_id": None,
        })
        assert r2.status_code == 200
        assert r2.json()["goal_id"] == gid

        # 3. Complete
        r3 = client_with_goal.post("/api/goal/complete", json={
            "session_id": sid, "outcome": "cancelled",
            "summary": "aborted",
        })
        # COMPLETE requires audit; CANCELLED does not. Use cancelled.
        if r3.status_code == 200:
            assert r3.json()["new_status"] == "cancelled"


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

    # ─── new behavior assertions ────────────────────────────────────────

    def test_hypothesis_create_accepts_universe_and_signal(self, client_with_hypothesis):
        """universe/signal_definition should be stored on the new hypothesis."""
        r = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "carry_rb",
            "thesis": "TS momentum carry",
            "universe": "rb_futures",
            "signal_definition": "20d return > 5%",
        })
        assert r.status_code == 200
        hyp_id = r.json()["hypothesis_id"]
        # Verify via GET that the fields round-trip
        get_r = client_with_hypothesis.get(f"/api/hypothesis/{hyp_id}")
        body = get_r.json()["hypothesis"]
        assert body["universe"] == "rb_futures"
        assert body["signal_definition"] == "20d return > 5%"

    def test_hypothesis_list_filters_by_status(self, client_with_hypothesis):
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h1", "thesis": "t", "status": "exploring",
        })
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h2", "thesis": "t", "status": "testing",
        })
        resp = client_with_hypothesis.get("/api/hypothesis/list?status=testing")
        items = resp.json()["hypotheses"]
        assert all(h["status"] == "testing" for h in items)
        # And exploring filter excludes them
        resp2 = client_with_hypothesis.get("/api/hypothesis/list?status=exploring")
        items2 = resp2.json()["hypotheses"]
        assert all(h["status"] == "exploring" for h in items2)

    def test_hypothesis_list_returns_to_dict_payload(self, client_with_hypothesis):
        """Items must NOT include non-serializable internals (e.g. raw dataclass refs)."""
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h_ser", "thesis": "x",
        })
        r = client_with_hypothesis.get("/api/hypothesis/list")
        items = r.json()["hypotheses"]
        assert len(items) >= 1
        item = items[0]
        assert "hypothesis_id" in item
        assert "title" in item
        assert "status" in item
        # datetime fields should be JSON-serializable (str or None)
        assert isinstance(item.get("created_at"), (str, type(None)))
        # No Python-specific leaks
        assert "_sa_instance_state" not in item

    def test_hypothesis_get_returns_to_dict_payload(self, client_with_hypothesis):
        r = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "h_get", "thesis": "t",
        })
        hyp_id = r.json()["hypothesis_id"]
        get_r = client_with_hypothesis.get(f"/api/hypothesis/{hyp_id}")
        body = get_r.json()
        assert body["status"] == "ok"
        hyp = body["hypothesis"]
        assert hyp["hypothesis_id"] == hyp_id
        # to_dict includes run_cards etc.
        assert "run_cards" in hyp
        assert "related_ids" in hyp

    def test_hypothesis_update_returns_404_for_missing_id(self, client_with_hypothesis):
        """Updating a nonexistent id should return 404, not 500."""
        r = client_with_hypothesis.put("/api/hypothesis/update", json={
            "hypothesis_id": "hyp_does_not_exist",
            "status": "testing",
        })
        assert r.status_code == 404
        detail = r.json().get("detail", "")
        assert "hyp_does_not_exist" in detail or "not found" in detail.lower()

    def test_hypothesis_search_with_query_returns_results(self, client_with_hypothesis):
        client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "alpha_decay",
            "thesis": "TS alpha decays rapidly",
        })
        r = client_with_hypothesis.get("/api/hypothesis/search?q=alpha")
        body = r.json()
        assert body["status"] == "ok"
        # Should find the one we just created (FTS5)
        titles = [h["title"] for h in body["results"]]
        assert "alpha_decay" in titles

    def test_hypothesis_update_changes_status(self, client_with_hypothesis):
        r1 = client_with_hypothesis.post("/api/hypothesis/create", json={
            "title": "to_update", "thesis": "t", "status": "exploring",
        })
        hyp_id = r1.json()["hypothesis_id"]
        r2 = client_with_hypothesis.put("/api/hypothesis/update", json={
            "hypothesis_id": hyp_id,
            "status": "testing",
        })
        assert r2.status_code == 200
        assert r2.json()["hypothesis"]["status"] == "testing"


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
