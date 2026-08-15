"""Phase 5 tests — action matrix + available_actions + unified dispatch + redo.

Covers:
- ``allowed_actions`` matrix per status
- GET /{id}/available_actions (status-dependent list)
- POST /{id}/actions/{name} dispatch + 409 when not allowed
- POST /{id}/rounds/{n}/redo: DB row + state.json + round dir rewind
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from strategy_research.core.study.models import (
    StudyAction,
    StudyStatus,
    allowed_actions,
)


def _bearer(user_id: str = "tester") -> dict:
    from strategy_research.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


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
def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    sessions_db = tmp_path / "sessions.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('sess-5', 'tester', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()
    from strategy_research.core.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-5", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    return rec.study_id, tmp_path


# ── action matrix ────────────────────────────────────────────────────


class TestActionMatrix:
    def test_running_allows_pause_cancel(self):
        acts = allowed_actions(StudyStatus.RUNNING)
        assert StudyAction.PAUSE in acts
        assert StudyAction.CANCEL in acts
        assert StudyAction.RESUME not in acts

    def test_paused_allows_resume_cancel(self):
        acts = allowed_actions(StudyStatus.PAUSED)
        assert StudyAction.RESUME in acts
        assert StudyAction.CANCEL in acts

    def test_interrupted_allows_only_resume_interrupted(self):
        acts = allowed_actions(StudyStatus.INTERRUPTED)
        assert acts == frozenset({StudyAction.RESUME_INTERRUPTED})

    def test_terminal_statuses_have_no_actions(self):
        for st in (StudyStatus.COMPLETE, StudyStatus.CANCELLED,
                   StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
                   StudyStatus.EARLY_STOPPED, StudyStatus.NEEDS_REFRESH):
            assert allowed_actions(st) == frozenset(), st


# ── HTTP: available_actions + dispatch ───────────────────────────────


@pytest.mark.asyncio
async def test_available_actions_matches_matrix(_env):
    study_id, tmp_path = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/available_actions")
        assert r.status_code == 200
        body = r.json()
        assert body["execution_status"] == StudyStatus.QUEUED.value
        names = {a["name"] for a in body["actions"]}
        assert names == {StudyAction.CANCEL.value}
        assert body["actions"][0]["destructive"] is True


@pytest.mark.asyncio
async def test_dispatch_cancel_not_allowed_returns_409(_env):
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # QUEUED → pause is not in the matrix → 409
        r = await client.post(f"/api/study/{study_id}/actions/pause")
        assert r.status_code == 409
        # QUEUED → cancel IS allowed but scheduler has no token yet → 409
        # (scheduler.pause/cancel returns False when no control token)
        r2 = await client.post(f"/api/study/{study_id}/actions/cancel")
        assert r2.status_code in (200, 409)


@pytest.mark.asyncio
async def test_dispatch_unknown_action_404(_env):
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(f"/api/study/{study_id}/actions/bogus")
        assert r.status_code == 404


# ── redo ─────────────────────────────────────────────────────────────


def test_scheduler_redo_rewinds_state_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    import asyncio

    from strategy_research.core.study import StudyScheduler, StudyStore
    from strategy_research.core.study import state_store as ss

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="s1", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    sid = rec.study_id
    # Simulate: round 3 completed (DB row + state.json + round dir).
    store.append_round(sid, 3, "run_0001", metrics={"calmar": 1.0}, verdict="keep")
    store.update_round_heartbeat(sid, 3)
    ws = tmp_path / "study" / sid
    (ws / "rounds" / "round_0003" / "run_0001").mkdir(parents=True)
    (ws / "rounds" / "round_0003" / "run_0001" / "strategy.py").write_text("PARAMS={}\n")
    st = ss.StudyState()
    st.last_completed_round = 3
    st.last_keep_run_dir = "rounds/round_0003/run_0001"
    ss.save(tmp_path, sid, st)

    sched = StudyScheduler(store, session_service=None)

    async def main():
        ok = await sched.redo(sid, 3, workspace_path=str(tmp_path))
        assert ok is True
        # DB round row gone + current_round rewound
        assert store.get_round(sid, 3) is None
        assert store.get_study(sid).current_round == 2
        # state rewound
        st2 = ss.load(tmp_path, sid)
        assert st2.last_completed_round == 2
        assert st2.last_keep_run_dir is None
        # round dir removed
        assert not (ws / "rounds" / "round_0003").exists()
        # re-queued for execution
        assert store.get_study(sid).execution_status == StudyStatus.QUEUED
        await sched.shutdown()

    asyncio.run(main())


def test_scheduler_redo_rejects_running(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    import asyncio

    from strategy_research.core.study import StudyScheduler, StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="s1", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    store.update_execution_status(rec.study_id, StudyStatus.RUNNING)
    sched = StudyScheduler(store, session_service=None)

    async def main():
        ok = await sched.redo(rec.study_id, 2, workspace_path=str(tmp_path))
        assert ok is False
        await sched.shutdown()

    asyncio.run(main())


# ── G1: per-user concurrency cap ─────────────────────────────────────


def test_per_user_cap_blocks_third_study(tmp_path, monkeypatch):
    """G1: SR_STUDY_MAX_PER_USER=2 — 用户 A 第 3 个 study 被 per-user
    semaphore 挡住（全局 semaphore 用高值避免干扰）。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_STUDY_MAX_CONCURRENT", "10")
    monkeypatch.setenv("SR_STUDY_MAX_PER_USER", "2")
    import asyncio

    from strategy_research.core.study import StudyScheduler, StudyStore

    store = StudyStore()
    ids = []
    for i in range(3):
        rec = store.create_study(
            owner_session_id="user-a", goal_id=None, objective=f"o{i}",
            workspace_path=str(tmp_path), strategy_name="demo",
        )
        ids.append(rec.study_id)

    sched = StudyScheduler(store, session_service=None)
    # 直接占用 2 个 per-user 名额（模拟已在运行）
    s1 = sched._user_semaphores.setdefault("user-a", asyncio.Semaphore(2))
    await_got = []

    async def main():
        await s1.acquire()
        await s1.acquire()
        # 第 3 个应该被阻塞 —— 用 wait_for 确认超时
        owner = "user-a"
        sem = sched._user_semaphores.setdefault(owner, asyncio.Semaphore(2))
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.2)
            await_got.append(True)
        except asyncio.TimeoutError:
            await_got.append(False)
        # dump 反映 per-user 状态
        dump = sched.dump_concurrency()
        assert dump["per_user_limit"] == 2
        assert dump["per_user_active"].get("user-a") == 2
        await sched.shutdown()

    asyncio.run(main())
    assert await_got == [False], "第 3 个 study 不应获得 per-user 名额"


# ── E1: IDOR enforcement（SR_ENFORCE_STUDY_IDOR=1） ──────────────────


def test_idor_blocks_other_users_study(tmp_path, monkeypatch):
    """E1: 开启 IDOR 后，另一用户的 study 不可读/不可操作（403）。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    import sqlite3
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.core.study import StudyStore

    # sessions: owner-1 属于 alice，owner-2 属于 bob
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    for sid, uid in (("owner-1", "alice"), ("owner-2", "bob")):
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, updated_at) "
            "VALUES (?, ?, '2026-08-01T10:00:00', '2026-08-01T10:00:00')",
            (sid, uid),
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="owner-2", goal_id=None, objective="bob's study",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    study_id = rec.study_id

    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    # alice 试图读 bob 的 study → 403
    r = client.get(f"/api/study/{study_id}/summary")
    assert r.status_code == 403
    r2 = client.get(f"/api/study/{study_id}/rounds")
    assert r2.status_code == 403
    r3 = client.post(f"/api/study/{study_id}/actions/cancel")
    assert r3.status_code == 403
    # Newly-isolated control endpoints must also be 403 for non-owners.
    r4 = client.post(f"/api/study/{study_id}/pause")
    assert r4.status_code == 403
    r5 = client.post(f"/api/study/{study_id}/resume")
    assert r5.status_code == 403
    r6 = client.get(f"/api/study/{study_id}/directives")
    assert r6.status_code == 403
    r7 = client.post(
        f"/api/study/{study_id}/directive",
        json={"content": "redirect"},
    )
    assert r7.status_code == 403


def test_idor_allows_own_study(tmp_path, monkeypatch):
    """E1: 开启 IDOR 后，本人 study 正常可读。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    import sqlite3
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.core.study import StudyStore

    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('owner-1', 'alice', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="owner-1", goal_id=None, objective="alice's study",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    r = client.get(f"/api/study/{rec.study_id}/summary")
    assert r.status_code == 200
    assert r.json()["objective"] == "alice's study"
