"""Tests for the study executor: metric-target satisfaction + budget +
pause/cancel + shutdown reasons.

Drives ``AutoresearchExecutor`` with ``run_research_round`` patched to
return a controlled fake result (no autoresearch run). Cover:
  - shutdown: TARGETS_MET (immediate on first round)
  - shutdown: MAX_ROUNDS
  - shutdown: BUDGET_LIMITED (turn budget)
  - shutdown: CANCELLED (control token)
  - shutdown: PAUSED → RESUME continuation
  - goal ledger reaches COMPLETE with evidence on every criterion
  - events emitted via the EventEmitter
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from strategy_research.core.autoresearch import run_research_round
from strategy_research.core.goal import GoalStore
from strategy_research.core.goal.context import default_goal_criteria
from strategy_research.core.study.executor import (
    AutoresearchExecutor,
    ControlToken,
    ShutdownReason,
    meets_metric_targets,
)
from strategy_research.core.study.models import StudyStatus
from strategy_research.core.study.store import StudyStore


# ── fixtures ────────────────────────────────────────────────────────


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


class CollectingEmitter:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event: str, data: dict) -> None:
        self.events.append((session_id, event, data))


def _make_round_result(metrics: dict, *, round: int = 1, run: str = "run_0001",
                       verdict: str = "keep", stagnation: bool = False) -> dict:
    return {
        "round": round,
        "run_name": run,
        "run_dir": Path("/tmp/fake"),
        "metrics": metrics,
        "verdict": verdict,
        "decision": {"stagnation_triggered": stagnation,
                     "reason": "stale" if stagnation else "",
                     "to_dict": lambda: {"stagnation_triggered": stagnation}},
        "agent_outputs": {"researcher": {"ok": True}, "data_quality": {"ok": True},
                          "factor_analyst": {"ok": True}, "strategist": {"ok": True},
                          "portfolio_construction": {"ok": True},
                          "risk_controller": {"ok": True},
                          "attribution_analyst": {"ok": True},
                          "anti_overfit_analyst": {"ok": True},
                          "backtest_diagnostics": {"ok": True}},
        "summary": {"round": round, "agent_statuses": {}, "performance_change": None,
                    "acceptance_decision": {"stagnation_triggered": stagnation}},
        "backtest_error": None,
    }


def _setup_with_goal(store: StudyStore, goal_store: GoalStore, **overrides):
    """Create a study + its goal ledger row."""

    goal = goal_store.replace_goal(
        session_id="sess-st",
        objective="研究动量",
        criteria=default_goal_criteria(),
    )
    kw = dict(
        session_id="sess-st",
        goal_id=goal.goal_id,
        objective="研究动量",
        workspace_path="/tmp/ws",
        strategy_name="rot_alpha",
        behavior="improving",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01,  # speed up tests
        cooldown_jitter=0.01,
        min_cooldown=0.01,
    )
    kw.update(overrides)
    study = store.create_study(**kw)
    return goal, study


# ── shutdown: TARGETS_MET ───────────────────────────────────────────


class TestExecutorShutdown:
    def test_targets_met_completes_goal_and_study(
        self, store, goal_store, monkeypatch
    ):
        goal, study = _setup_with_goal(store, goal_store)
        # Round always returns "good" metrics satisfying the target.
        calls = {"n": 0}

        def fake_round(self, r, prev):
            calls["n"] += 1
            return _make_round_result(
                {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            )

        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", fake_round,
        )
        # Also stub _round_cooldown so the loop doesn't wait.
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        emitter = CollectingEmitter()
        ex = AutoresearchExecutor(
            study, store, goal_store=goal_store, emitter=emitter,
        )
        reason = asyncio.run(ex.run())
        assert reason == ShutdownReason.TARGETS_MET
        assert calls["n"] == 1
        # study landed COMPLETE
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.COMPLETE
        assert got.last_metrics == {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1}
        assert got.completed_at != ""
        # goal landed COMPLETE
        g = goal_store.get_goal(goal.goal_id)
        assert g is not None
        assert g.status.value == "complete"
        # events: started + round + completed + executor_stopped
        ev_names = [e[1] for e in emitter.events]
        assert "study_started" in ev_names
        assert "study_round" in ev_names
        assert "study_completed" in ev_names
        assert "study_executor_stopped" in ev_names

    def test_max_rounds_terminal(self, store, goal_store, monkeypatch):
        goal, study = _setup_with_goal(
            store, goal_store,
            max_rounds=2,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        )
        # Metrics far below target, so loop only stops on max_rounds.
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round",
            lambda self, r, prev: _make_round_result(
                {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2}, round=r,
                verdict="discard",
            ),
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        ex = AutoresearchExecutor(study, store, goal_store=goal_store)
        reason = asyncio.run(ex.run())
        assert reason == ShutdownReason.MAX_ROUNDS
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.ERROR
        assert "max_rounds" in (got.last_error or "")

    def test_budget_turn_limit_terminates(self, store, goal_store, monkeypatch):
        goal, study = _setup_with_goal(
            store, goal_store,
            budget_turn=9,  # exactly 1 round = 9 agents
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round",
            lambda self, r, prev: _make_round_result(
                {"calmar": 0.1}, round=r,
            ),
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        ex = AutoresearchExecutor(study, store, goal_store=goal_store)
        reason = asyncio.run(ex.run())
        assert reason == ShutdownReason.BUDGET
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.BUDGET_LIMITED

    def test_cancel_during_run(self, store, goal_store, monkeypatch):
        goal, study = _setup_with_goal(
            store, goal_store,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        )
        control = ControlToken()
        called = {"n": 0}

        def _round(self, r, prev):
            called["n"] += 1
            if called["n"] == 1:
                control.cancelled = True
            return _make_round_result({"calmar": 0.1}, round=r)

        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        ex = AutoresearchExecutor(
            study, store, goal_store=goal_store, control=control,
        )
        reason = asyncio.run(ex.run())
        # Cancel checked BEFORE the next round, not mid. So first round runs,
        # second iteration sees cancelled → stops.
        assert reason == ShutdownReason.CANCELLED
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.CANCELLED

    def test_pause_resumes(self, store, goal_store, monkeypatch):
        # Pause is observed after round 1; _wait_until_resumed is patched
        # to immediately clear the flag (we want to verify the loop emits
        # study_paused + study_resumed and continues round 2).
        goal, study = _setup_with_goal(
            store, goal_store,
            max_rounds=2,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
        )
        control = ControlToken()
        rounds = {"n": 0}
        paused_flag = {"seen": False}

        def _round(self, r, prev):
            rounds["n"] += 1
            if rounds["n"] == 1:
                control.paused = True
            return _make_round_result({"calmar": 0.1}, round=r)

        async def _wait(self):
            # simulate resume by clearing the flag and recording it
            paused_flag["seen"] = True
            control.paused = False

        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_wait_until_resumed", _wait,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        emitter = CollectingEmitter()
        ex = AutoresearchExecutor(
            study, store, goal_store=goal_store, control=control,
            emitter=emitter,
        )
        reason = asyncio.run(ex.run())
        # max_rounds=2 reached before targets → ERROR/max_rounds
        assert reason == ShutdownReason.MAX_ROUNDS
        assert rounds["n"] == 2  # resumed and ran another round
        assert paused_flag["seen"]
        ev_names = [e[1] for e in emitter.events]
        assert "study_paused" in ev_names
        assert "study_resumed" in ev_names


class TestExecutorHelpers:
    def test_meets_metric_targets(self):
        assert meets_metric_targets(
            {"calmar": 0.6}, [{"name": "calmar", "op": ">=", "value": 0.5}],
        )
        assert not meets_metric_targets(
            {"calmar": 0.4}, [{"name": "calmar", "op": ">=", "value": 0.5}],
        )
        assert not meets_metric_targets(
            {}, [{"name": "calmar", "op": ">=", "value": 0.5}],
        )
        assert meets_metric_targets(
            {"max_dd": -0.05}, [{"name": "max_dd", "op": ">=", "value": -0.15}],
        )
        assert not meets_metric_targets(
            {"max_dd": -0.20}, [{"name": "max_dd", "op": ">=", "value": -0.15}],
        )

    def test_preserved_round_complete_with_missing_evidence_counts(
        self, store, goal_store, monkeypatch
    ):
        # When agents are stubbed and metrics satisfy targets on round 1
        # but the goal has 3 criteria, _complete_goal must add evidence to
        # the remaining criteria before completing. Validate outcome: goal
        # is COMPLETE with audit row.
        goal, study = _setup_with_goal(store, goal_store,
            metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}])
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round",
            lambda self, r, prev: _make_round_result(
                {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            ),
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        ex = AutoresearchExecutor(study, store, goal_store=goal_store)
        reason = asyncio.run(ex.run())
        assert reason == ShutdownReason.TARGETS_MET
        # Every required criterion must have evidence now.
        evidence = goal_store.list_evidence(goal.goal_id)
        crit_ids = {c.criterion_id for c in goal_store.list_criteria(goal.goal_id) if c.required}
        ev_crit_ids = {ev.criterion_id for ev in evidence if ev.criterion_id}
        assert crit_ids <= ev_crit_ids, {
            "missing evidence for": crit_ids - ev_crit_ids, "evidence has": ev_crit_ids,
        }