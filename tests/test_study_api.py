"""Smoke tests for the ``/api/study/*`` HTTP routes — shape-only.

The scheduler's end-to-end loop (submit → executor → complete) is
covered by ``test_study_scheduler.py``; here we verify the HTTP layer
parses requests, validates workspace/strategy, returns 404 for unknown
control targets, and the list endpoint works without a session filter.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI
    from strategy_research.api.middleware import AuthMiddleware
    from strategy_research.api.routers import chat, study
    from strategy_research.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    return app


@pytest.fixture
def _app_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    ws = tmp_path / "ws"
    strat_dir = ws / "strategies" / "demo_strategy"
    strat_dir.mkdir(parents=True)
    (strat_dir / "strategy.py").write_text(
        "PARAMS = {}\nFACTOR_EXPRS = []\nFACTOR_WEIGHT_METHOD = 'equal'\n",
        encoding="utf-8",
    )
    (ws / "acceptance.yaml").write_text("llm_enabled: false\n", encoding="utf-8")
    return ws


@pytest.mark.asyncio
async def test_start_rejects_missing_workspace(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        body = {
            "session_id": "no-such", "objective": "x",
            "workspace_path": "/tmp/no-such-ws-xyz", "strategy_name": "demo",
        }
        r = await client.post("/api/study/start", json=body)
        assert r.status_code == 400
        assert "does not exist" in r.json()["detail"]


@pytest.mark.asyncio
async def test_start_auto_creates_strategy(_app_env):
    """When strategy dir doesn't exist, it should be auto-created.
    
    Note: We can't fully test the start endpoint because it triggers the
    scheduler in the background. Instead, test the auto-creation logic directly.
    """
    from pathlib import Path
    from strategy_research.api.routers.study import _create_minimal_strategy

    strat_dir = _app_env / "strategies" / "auto_created_strat"
    assert not strat_dir.exists()
    strat_dir.mkdir(parents=True)
    _create_minimal_strategy(strat_dir, "auto_created_strat")
    assert (strat_dir / "strategy.py").exists()
    content = (strat_dir / "strategy.py").read_text()
    assert "auto_created_strat" in content


@pytest.mark.asyncio
async def test_cancel_unknown_returns_404(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post("/api/study/unknown_study/pause")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_returns_all_empty(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get("/api/study/list")
        assert r.status_code == 200
        assert "studies" in r.json()
        assert r.json()["studies"] == []


@pytest.mark.asyncio
async def test_list_invalid_status_returns_400(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get("/api/study/list?status=bogus")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_status_no_study(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get("/api/study/status?session_id=no-such-session")
        assert r.status_code == 200
        assert r.json()["status"] == "no_study"


@pytest.mark.asyncio
async def test_summary_returns_strategy_and_round_fields(_app_env, tmp_path, monkeypatch):
    """summary 端点应返回 strategy_name/workspace_path/timestamps，
    且不因 round 记录缺 factor_failures 而 500。"""
    from strategy_research.core.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            session_id="sess-1",
            goal_id=None,
            objective="test objective",
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            max_rounds=5,
        )
        store.append_round(
            study.study_id, 1, "run_0001",
            metrics={"sharpe": 1.5}, verdict="keep",
        )
        study_id = study.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    app = _build_asgi_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["strategy_name"] == "demo_strategy"
        assert data["workspace_path"] == str(_app_env)
        assert data["created_at"]
        assert data["updated_at"]
        assert len(data["recent_rounds"]) == 1
        assert data["recent_rounds"][0]["run_name"] == "run_0001"
        assert data["recent_rounds"][0]["metrics"] == {"sharpe": 1.5}


def _seed_study(_app_env, tmp_path, monkeypatch, *, objective="test objective",
                goal_id=None):
    """Create a study row in the goals.db used by the API layer."""
    from strategy_research.core.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            session_id="sess-1",
            goal_id=goal_id,
            objective=objective,
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            max_rounds=5,
        )
        return study.study_id


def _api_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_build_asgi_app()),
                             base_url="http://test")


@pytest.mark.asyncio
async def test_directive_success_returns_directive_id(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)

    async with _api_client() as client:
        r = await client.post(
            f"/api/study/{study_id}/directive",
            json={"content": "  加大动量权重  ", "issued_by": "tester"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["directive_id"].startswith("dir_")
        assert data["created_at"]

        # Round-trip through the list endpoint (content is trimmed).
        r2 = await client.get(f"/api/study/{study_id}/directives")
        assert r2.status_code == 200
        directives = r2.json()["directives"]
        assert len(directives) == 1
        assert directives[0]["content"] == "加大动量权重"
        assert directives[0]["issued_by"] == "tester"
        assert directives[0]["consumed_at"] is None


@pytest.mark.asyncio
async def test_directive_empty_content_returns_400(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)

    async with _api_client() as client:
        r = await client.post(
            f"/api/study/{study_id}/directive",
            json={"content": "   ", "issued_by": "tester"},
        )
        assert r.status_code == 400
        assert "must not be empty" in r.json()["detail"]


@pytest.mark.asyncio
async def test_directive_unknown_study_returns_404(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.post(
            "/api/study/ghost-study/directive",
            json={"content": "hi", "issued_by": "tester"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_directives_list_unknown_study_returns_404(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/ghost-study/directives")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_summary_goal_snapshot_and_scoreboard(_app_env, tmp_path, monkeypatch):
    """summary 应带 goal_snapshot（进度/标准/证据数）与 journal 派生的 scoreboard。"""
    from strategy_research.core.goal import GoalStore
    from strategy_research.core.goal.models import EvidenceInput
    from strategy_research.core.goal.store import RiskTier

    db_path = tmp_path / "goals.db"
    with GoalStore(db_path=db_path) as gs:
        goal = gs.replace_goal(
            session_id="sess-1",
            objective="Sharpe 提升目标",
            criteria=["Sharpe > 1.0", "回撤 < 20%"],
            risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        gs.append_evidence(
            session_id="sess-1",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text="动量因子提升 Sharpe",
                artifact_path="/tmp/ws/strategies/demo/strategy.py",
                assumptions={"sharpe": 1.3},
            ),
        )
        gs.append_journal_entry(
            goal_id=goal.goal_id, session_id="sess-1", round_num=1,
            hypothesis_id="hyp-1", label="动量假设",
            levers=["momentum"],
        )
        gs.fill_journal_attribution(
            goal_id=goal.goal_id, session_id="sess-1", round_num=1,
            outcome="accepted", attribution={"sharpe": 1.3},
        )
        gs.append_journal_entry(
            goal_id=goal.goal_id, session_id="sess-1", round_num=2,
            hypothesis_id="hyp-2", label="反转假设",
            levers=["reversal"],
        )
        gs.fill_journal_attribution(
            goal_id=goal.goal_id, session_id="sess-1", round_num=2,
            outcome="reverted", attribution={},
        )
        goal_id = goal.goal_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch, goal_id=goal_id)

    async with _api_client() as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        data = r.json()

        snap = data["goal_snapshot"]
        assert snap["goal_id"] == goal_id
        assert snap["objective"] == "Sharpe 提升目标"
        assert snap["evidence_count"] == 1
        assert snap["progress_percent"] >= 0

        scoreboard = data["scoreboard"]
        by_lever = {row["lever"]: row for row in scoreboard}
        assert by_lever["momentum"]["attempts"] == 1
        assert by_lever["momentum"]["accepted"] == 1
        assert by_lever["momentum"]["precision_mean"] == 1.0
        assert by_lever["reversal"]["attempts"] == 1
        assert by_lever["reversal"]["reverted"] == 1
        assert by_lever["reversal"]["precision_mean"] == 0.0


@pytest.mark.asyncio
async def test_list_filter_by_session_id(_app_env, tmp_path, monkeypatch):
    """list?session_id= 应只返回该 session 的 study。"""
    from strategy_research.core.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s_a = store.create_study(
            session_id="sess-A", goal_id=None, objective="A obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.create_study(
            session_id="sess-B", goal_id=None, objective="B obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        study_a_id = s_a.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/list?session_id=sess-A")
        assert r.status_code == 200
        data = r.json()
        assert len(data["studies"]) == 1
        assert data["studies"][0]["study_id"] == study_a_id
        assert data["studies"][0]["session_id"] == "sess-A"


@pytest.mark.asyncio
async def test_list_filter_by_status_returns_matching_only(
    _app_env, tmp_path, monkeypatch
):
    """list?status=running 应只返回该 execution_status 的 study。"""
    from strategy_research.core.study import StudyStatus, StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s_queued = store.create_study(
            session_id="sess-1", goal_id=None, objective="queued obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        s_running = store.create_study(
            session_id="sess-1", goal_id=None, objective="running obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.update_execution_status(s_running.study_id, StudyStatus.RUNNING)
        store.update_execution_status(
            s_queued.study_id, StudyStatus.COMPLETE,
            last_metrics={"sharpe": 1.2}, last_verdict="keep",
        )

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        # status=running → 只有 s_running
        r = await client.get("/api/study/list?status=running")
        assert r.status_code == 200
        rows = r.json()["studies"]
        assert len(rows) == 1
        assert rows[0]["execution_status"] == "running"
        assert rows[0]["last_verdict"] is None

        # status=complete → 只有 s_queued（已 complete）
        r2 = await client.get("/api/study/list?status=complete")
        rows2 = r2.json()["studies"]
        assert len(rows2) == 1
        assert rows2[0]["execution_status"] == "complete"
        assert rows2[0]["last_verdict"] == "keep"


@pytest.mark.asyncio
async def test_list_limit_caps_results(_app_env, tmp_path, monkeypatch):
    """list?limit=N 应最多返回 N 条 study。"""
    from strategy_research.core.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        for i in range(5):
            store.create_study(
                session_id=f"sess-{i}", goal_id=None,
                objective=f"obj {i}",
                workspace_path=str(_app_env), strategy_name="demo",
                executor_type="autoresearch", max_rounds=3,
            )

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/list?limit=2")
        assert r.status_code == 200
        assert len(r.json()["studies"]) == 2
