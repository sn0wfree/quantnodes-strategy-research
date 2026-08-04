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


def _round_lambda(metrics, verdict="keep", stagnation=False):
    """Build a 3-or-4-arg compatible fake round function.

    Accepts the old ``(self, r, prev)`` signature as well as the new
    ``(self, r, prev, directives_text)`` one.
    """
    def _fake(self, r, prev, directives_text=None):
        return _make_round_result(metrics, round=r, verdict=verdict,
                                 stagnation=stagnation)
    return _fake


# ── shutdown: TARGETS_MET ───────────────────────────────────────────


class TestExecutorShutdown:
    def test_targets_met_completes_goal_and_study(
        self, store, goal_store, monkeypatch
    ):
        goal, study = _setup_with_goal(store, goal_store)
        # Round always returns "good" metrics satisfying the target.
        calls = {"n": 0}

        def fake_round(self, r, prev, directives_text=None):
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
            _round_lambda(
                {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2},
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
            _round_lambda({"calmar": 0.1}),
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

        def _round(self, r, prev, directives_text=None):
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

        def _round(self, r, prev, directives_text=None):
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
            lambda self, r, prev, directives_text=None: _make_round_result(
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


# ── Phase 2: directive injection ────────────────────────────────────


class TestDirectiveInjection:
    def test_pending_directive_passed_to_round(self, store, monkeypatch):
        # Use a study without metric targets so the executor doesn't bail
        # out after one round — it should run round 1, then round 2, etc.
        from strategy_research.core.study import StudyStatus
        study = store.create_study(
            session_id="sess-st",
            goal_id=None,
            objective="研究动量",
            workspace_path="/tmp/ws",
            strategy_name="rot_alpha",
            cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
            metric_targets=[],   # ← disable auto-completion
            max_rounds=2,
        )
        captured: list = []

        def _round(self, r, prev, directives_text=None):
            captured.append(directives_text)
            return _make_round_result(
                {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2}, round=r,
            )

        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        # Round 1: no directive yet
        # Round 2: directive added between rounds → consumed in round 2
        # We simulate this by re-enqueuing after executor has read pending.
        ex = AutoresearchExecutor(study, store, emitter=__import__(
            "strategy_research.core.study.executor",
            fromlist=["NullEmitter"],
        ).NullEmitter())

        # Pre-add directive → round 1 will see it
        store.add_directive(study.study_id, "改用动量因子", issued_by="chat:test")
        asyncio.run(ex.run())

        # Round 1 captured the directive text; round 2 captured None
        # (directive was marked consumed after round 1).
        assert len(captured) == 2
        assert "改用动量因子" in (captured[0] or "")
        assert captured[1] is None
        # All directives consumed
        assert store.list_pending_directives(study.study_id) == []

    def test_format_directives(self):
        from dataclasses import dataclass
        from strategy_research.core.study.executor import AutoresearchExecutor
        @dataclass
        class D:
            content: str
            created_at: str = "2026-08-04T10:00:00+00:00"
        out = AutoresearchExecutor._format_directives(
            [D("focus on volatility"), D("use small caps")]
        )
        assert "<user-directives>" in out
        assert "</user-directives>" in out
        assert "focus on volatility" in out
        assert "use small caps" in out

    def test_empty_directives_list_format(self):
        """Empty input still wraps in user-directives block (no items)."""
        from strategy_research.core.study.executor import AutoresearchExecutor
        out = AutoresearchExecutor._format_directives([])
        assert out.startswith("<user-directives>")
        assert "no directives" not in out  # still a valid block
        # The executors guard (`if pending_directives` before calling
        # _format_directives) is what actually keeps an empty list out
        # of the round's prompt — covered by the integration test
        # (test_pending_directive_passed_to_round).


# ── Phase 3: monitoring loop ──────────────────────────────────────────


class TestMonitoringLoop:
    def test_complete_then_monitor_transitions_status(
        self, store, goal_store, monkeypatch
    ):
        """A study with monitor_interval should launch monitor background task."""
        control = ControlToken()
        def _round(self, r, prev, directives_text=None):
            return _make_round_result(
                {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        checks = {"n": 0}
        def _monitor_check(self):
            checks["n"] += 1
            from datetime import datetime, timezone
            # After first check, request cancel so the loop exits cleanly.
            control.cancelled = True
            return {
                "metrics": {"calmar": 0.6, "sharpe": 0.4, "max_dd": -0.1},
                "verdict": "monitor",
                "meets_targets": True,
                "reason": "",
                "now_iso": datetime.now(timezone.utc).isoformat(),
            }
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_monitor_check", _monitor_check,
        )
        async def _fast_sleep(self, interval):
            # skip the wait entirely
            return None
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_monitor_sleep", _fast_sleep,
        )

        goal, study = _setup_with_goal(store, goal_store,
            monitor_interval_seconds=60,
        )
        ex = AutoresearchExecutor(study, store, goal_store=goal_store,
                                  control=control)

        async def _run_and_wait():
            reason = await ex.run()
            assert reason == ShutdownReason.MONITORING
            # Wait for the background monitor task to complete
            if ex._monitor_task is not None:
                await ex._monitor_task

        asyncio.run(_run_and_wait())
        assert checks["n"] >= 1
        got = store.get_study(study.study_id)
        # No drift detected → status remains MONITORING when cancelled.
        assert got.execution_status in (
            StudyStatus.MONITORING, StudyStatus.CANCELLED,
        )

    def test_drift_triggers_needs_refresh(
        self, store, goal_store, monkeypatch
    ):
        """Monitor finds a regression → NEEDS_REFRESH."""
        def _round(self, r, prev, directives_text=None):
            return _make_round_result(
                {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )
        async def _fast_sleep(self, interval):
            return None
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_monitor_sleep", _fast_sleep,
        )

        # Drift on first check — metrics too low to meet targets.
        def _monitor_check(self):
            from datetime import datetime, timezone
            return {
                "metrics": {"calmar": 0.1, "sharpe": 0.0, "max_dd": -0.2},
                "verdict": "monitor",
                "meets_targets": False,
                "reason": "calmar below threshold",
                "now_iso": datetime.now(timezone.utc).isoformat(),
            }
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_monitor_check", _monitor_check,
        )

        goal, study = _setup_with_goal(store, goal_store,
            monitor_interval_seconds=60,
        )
        ex = AutoresearchExecutor(study, store, goal_store=goal_store)

        async def _run_and_wait():
            reason = await ex.run()
            assert reason == ShutdownReason.MONITORING
            # Wait for the background monitor task to complete
            if ex._monitor_task is not None:
                await ex._monitor_task

        asyncio.run(_run_and_wait())
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.NEEDS_REFRESH
        assert got.monitor_drift_count == 1

    def test_no_monitor_when_interval_none(
        self, store, goal_store, monkeypatch
    ):
        """No monitor_interval → study stays COMPLETE."""
        def _round(self, r, prev, directives_text=None):
            return _make_round_result(
                {"calmar": 0.62, "sharpe": 0.41, "max_dd": -0.1},
            )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_run_one_round", _round,
        )
        monkeypatch.setattr(
            "strategy_research.core.study.executor.AutoresearchExecutor."
            "_round_cooldown", lambda self: 0.0,
        )

        goal, study = _setup_with_goal(store, goal_store)  # no monitor_interval
        ex = AutoresearchExecutor(study, store, goal_store=goal_store)
        asyncio.run(ex.run())
        got = store.get_study(study.study_id)
        assert got.execution_status == StudyStatus.COMPLETE