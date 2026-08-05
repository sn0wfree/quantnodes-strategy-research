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
