"""StudyScheduler tests — submit → run → complete, pause/cancel, recover.

Uses a stubbed ``AutoresearchExecutor._run_one_round`` so runs are
instant and deterministic. ``SessionService`` isolation uses a tiny
fake: only ``is_session_processing`` / ``mark_session_processing`` /
``event_bus.emit`` are exercised.

A whole study lifecycle happens inside one ``asyncio.run`` so the
scheduler's per-session consumer task (created via ``create_task``) is
not stranded when a subsequent ``asyncio.run`` swaps the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from strategy_research.core.goal import GoalStore
from strategy_research.core.goal.context import default_goal_criteria
from strategy_research.core.study import (
    StudyScheduler, StudyStatus, StudyStore,
)
from strategy_research.core.study import executor as executor_mod
from strategy_research.core.study import runner as runner_mod


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db")
    )
    monkeypatch.setenv(
        "QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json")
    )


@pytest.fixture
def store(tmp_path: Path):
    return StudyStore(db_path=tmp_path / "goals.db")


@pytest.fixture
def goal_store():
    return GoalStore()


class _FakeBus:
    def emit(self, sid, event, data):
        pass


class FakeSessionService:
    """Minimal stand-in for SessionService mutex primitives + event_bus."""

    def __init__(self):
        self._processing: set[str] = set()
        self.event_bus = _FakeBus()

    def is_session_processing(self, sid: str) -> bool:
        return sid in self._processing

    def mark_session_processing(self, sid: str, *, processing: bool) -> None:
        if processing:
            self._processing.add(sid)
        else:
            self._processing.discard(sid)


def _setup(store, goal_store, **overrides):
    """Create a study + its goal ledger row."""
    goal = goal_store.replace_goal(
        session_id="sess-st",
        objective="研究动量",
        criteria=default_goal_criteria(),
    )
    kw = dict(
        session_id="sess-st", goal_id=goal.goal_id, objective="研究动量",
        workspace_path="/tmp/ws", strategy_name="rot_alpha",
        behavior="improving",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    kw.update(overrides)
    study = store.create_study(**kw)
    return goal, study


def _patch_round(monkeypatch, metrics=None, rounds_counter=None):
    """Stub AutoresearchExecutor._run_one_round + cooldown + summary load."""

    def _round(self, r, prev, directives_text=None):
        if rounds_counter is not None:
            rounds_counter["n"] += 1
        m = metrics or {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1}
        return {
            "round": r, "run_name": f"run_{r:04d}", "run_dir": Path("/tmp/fake"),
            "metrics": m, "verdict": "keep",
            "decision": {"stagnation_triggered": False, "reason": "",
                         "to_dict": lambda: {"stagnation_triggered": False}},
            "agent_outputs": {k: {"ok": True} for k in
                ("researcher", "data_quality", "factor_analyst", "strategist",
                 "portfolio_construction", "risk_controller",
                 "attribution_analyst", "anti_overfit_analyst",
                 "backtest_diagnostics")},
            "summary": {"round": r, "agent_statuses": {}, "performance_change": None,
                        "acceptance_decision": {"stagnation_triggered": False}},
            "backtest_error": None,
        }

    monkeypatch.setattr(runner_mod.AutoresearchRunner, "_run_one_round", _round)
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_round_cooldown",
        lambda self: 0.0,
    )
    monkeypatch.setattr(
        runner_mod.AutoresearchRunner, "_maybe_load_previous_summary",
        lambda self, study: None,
    )


async def _await_status(store, study_id, target, *, timeout_steps=300, step=0.01):
    """Poll the store until status matches or the bound is exhausted."""
    last = None
    for _ in range(timeout_steps):
        await asyncio.sleep(step)
        cur = store.get_study(study_id)
        last = cur
        if cur and cur.execution_status == target:
            return cur
    return last


# ── submit → complete ───────────────────────────────────────────────


def test_submit_completes_store_updates(store, goal_store, monkeypatch):
    _patch_round(monkeypatch)
    goal, study = _setup(store, goal_store)
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        await sched.submit(study)
        cur = await _await_status(store, study.study_id, StudyStatus.COMPLETE)
        assert cur is not None, f"final status: {store.get_study(study.study_id).execution_status}"
        assert cur.execution_status == StudyStatus.COMPLETE
        # Chat slot released.
        assert not svc.is_session_processing("sess-st")
        await sched.shutdown()

    asyncio.run(main())
    g = goal_store.get_goal(goal.goal_id)
    assert g.status.value == "complete"


def test_concurrent_session_blocks_until_released(store, goal_store, monkeypatch):
    _patch_round(monkeypatch)
    goal, study = _setup(store, goal_store)
    svc = FakeSessionService()
    svc.mark_session_processing("sess-st", processing=True)  # chat busy
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        await sched.submit(study)
        # Study should NOT have entered running while chat is busy.
        await asyncio.sleep(0.05)
        cur = store.get_study(study.study_id)
        assert cur.execution_status == StudyStatus.QUEUED, cur.execution_status
        # Now release chat — study should proceed.
        svc.mark_session_processing("sess-st", processing=False)
        cur = await _await_status(store, study.study_id, StudyStatus.COMPLETE)
        assert cur is not None
        assert cur.execution_status == StudyStatus.COMPLETE
        await sched.shutdown()

    asyncio.run(main())


# ── pause / resume / cancel via scheduler ───────────────────────────


def test_cancel_via_scheduler(store, goal_store, monkeypatch):
    rounds = {"n": 0}
    _patch_round(monkeypatch, metrics={"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2},
                 rounds_counter=rounds)
    goal, study = _setup(store, goal_store,
        metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        max_rounds=None,
    )
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        await sched.submit(study)
        # Wait for running state.
        await _await_status(store, study.study_id, StudyStatus.RUNNING)
        # Cancel via scheduler control token.
        assert sched.cancel(study.study_id) is True
        cur = await _await_status(store, study.study_id, StudyStatus.CANCELLED)
        assert cur is not None and cur.execution_status == StudyStatus.CANCELLED
        await sched.shutdown()

    asyncio.run(main())


# ── startup recovery ────────────────────────────────────────────────


def test_recover_on_startup_reenqueues_running(store, goal_store, monkeypatch):
    _patch_round(monkeypatch)
    goal, study = _setup(store, goal_store)
    # Simulate leftover "running" row (process died mid-run).
    store.update_execution_status(study.study_id, StudyStatus.RUNNING)
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        recs = await sched.recover_on_startup()
        assert len(recs) == 1
        assert recs[0].study_id == study.study_id
        # NEW policy: running → interrupted (manual resume required)
        s = store.get_study(study.study_id)
        assert s.execution_status == StudyStatus.INTERRUPTED

        # Resume manually
        ok = await sched.resume_interrupted(study.study_id)
        assert ok is True

        # Eventually completes.
        cur = await _await_status(store, study.study_id, StudyStatus.COMPLETE)
        assert cur is not None and cur.execution_status == StudyStatus.COMPLETE
        await sched.shutdown()

    asyncio.run(main())


def test_recover_on_startup_respects_paused(store, goal_store, monkeypatch):
    goal, study = _setup(store, goal_store)
    store.update_execution_status(study.study_id, StudyStatus.PAUSED)
    svc = FakeSessionService()
    sched = StudyScheduler(store, session_service=svc)

    async def main():
        recs = await sched.recover_on_startup()
        assert recs == []
        await sched.shutdown()

    asyncio.run(main())
    assert store.get_study(study.study_id).execution_status == StudyStatus.PAUSED