"""Tests for run_round_phases — langgraph branch mapping (P0-1 fix).

Pre-fix: the langgraph engine returned early from run_round_phases,
skipping the entire finalization pipeline (results.tsv, manifest,
state.json, journal, DB mirror, review cycle, e2_passed). Studies
using the langgraph engine could never advance past
last_completed_round=0.

Post-fix: the langgraph result is mapped to the same variables the
phases engine produces, then falls through to the shared finalization.

These tests verify the mapping contract: variable assignments, the
paused_for_approval early-return, and the fall-through to finalization.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.study.phase_engine import run_round_phases
from strategy_research.core.study.runner import AutoresearchRunner


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Workspace + study directory + baseline strategy."""
    (tmp_path / "strategies" / "demo").mkdir(parents=True)
    (tmp_path / "strategies" / "demo" / "strategy.py").write_text(
        "PARAMS = {}\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def env(monkeypatch, ws: Path):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(ws / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(ws / "h.json"))
    return ws


def _make_study_record(ws: Path, *, engine: str = "langgraph", verdict: str = "keep"):
    """Build a minimal StudyRecord mock that satisfies run_round_phases's needs."""
    rec = MagicMock()
    rec.study_id = "study-st-1"
    rec.session_id = "study-st-1"
    rec.workspace_path = str(ws)
    rec.strategy_name = "demo"
    rec.engine = engine
    rec.executor_type = "autoresearch"
    rec.behavior = None
    rec.metric_targets = [{"name": "calmar", "op": ">=", "value": 0.5}]
    rec.budget_turn = 100
    rec.budget_time_seconds = 3600
    rec.cooldown_base = 0.01
    rec.cooldown_jitter = 0.01
    rec.min_cooldown = 0.01
    rec.max_rounds = None
    rec.early_stop_patience = 3
    rec.lazy_detection_interval = 10
    rec.keep_recent = 10
    rec.monitor_interval_seconds = None
    rec.goal_id = None
    rec.objective = "test objective"
    rec.archived_at = None
    return rec


def _make_runner(ws: Path, study_record):
    """Build a runner double that exposes the attrs run_round_phases reads.

    Plain MagicMock (no spec) so private attrs like _goal_store are
    auto-generated as further MagicMocks; run_round_phases calls methods
    on them and we assert on the important ones via the test.
    """
    runner = MagicMock()
    runner._get_study.return_value = study_record
    runner._load_graph.return_value = MagicMock(nodes=[], edges=[])
    runner._emit = MagicMock()
    runner._update_results_tsv = MagicMock()
    runner._emit_topology = MagicMock()
    runner._account_round_budget = MagicMock()
    runner._collect_knowledge = MagicMock()
    runner._novelty_gate = MagicMock(return_value=True)
    runner._check_novelty = MagicMock(return_value=(True, "novel"))
    runner._archive_rejected = MagicMock()
    runner._check_regression = MagicMock(return_value=(True, None))
    runner._record_keep_evidence = MagicMock()
    runner._build_journal_context = MagicMock(return_value="")
    runner._build_scoreboard_context = MagicMock(return_value="")
    runner._total_used_turns = 0
    runner._total_used_time = 0.0
    runner._prev_passed = set()
    runner._idle_rounds = 0
    # Pre-emptively wire _goal_store and _scoreboard so MagicMock doesn't
    # raise AttributeError when phase_engine accesses them.
    runner._goal_store = MagicMock()
    runner._scoreboard = MagicMock()
    runner.study_store = MagicMock()
    return runner


def _langgraph_result(*, paused: bool = False, verdict: str = "keep",
                      metrics: dict | None = None, researcher_hyp: str = "h"):
    """Build a result shaped like _rebuild_phase_outputs(agent_outputs)."""
    from strategy_research.core.strategy_acceptance import (
        AcceptanceDecision, decide as sa_decide,
    )
    metrics = metrics if metrics is not None else {"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.1}
    try:
        decision = sa_decide(metrics=metrics, llm_verdict=None)
    except Exception:
        decision = AcceptanceDecision(verdict=verdict, reason="")
    if paused:
        return {
            "round": 1,
            "run_name": "round_1",
            "paused_for_approval": True,
            "study_id": "study-st-1",
            "interrupt_id": "int-1",
        }
    return {
        "researcher_output": {"hypothesis": researcher_hyp, "action": "discover_local"},
        "data_quality_output": {"passed": True},
        "factor_analyst_output": {"recommendation": "ok"},
        "strategist_output": {"action": "discover_local", "changes": [{"param": "top_n", "old": 10, "new": 20}]},
        "portfolio_construction_output": {"method": "equal"},
        "backtest_result": {"metrics": metrics, "success": True},
        "backtest_error": None,
        "metrics": metrics,
        "risk_controller_output": {"risk_passed": True, "risk_rating": "Green"},
        "attribution_analyst_output": {"alpha": 0.02},
        "anti_overfit_analyst_output": {"verdict": "keep"},
        "backtest_diagnostics_output": {},
        "decision": decision,
        "verdict": verdict,
        "aoa_llm_verdict": {},
        "round": 1,
        "run_name": "round_1",
    }


# ── Tests ───────────────────────────────────────────────────


def test_langgraph_paused_returns_early(env, ws):
    """paused_for_approval=True → return without finalization."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result(paused=True)

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # Early return preserves only the langgraph keys
    assert result.get("paused_for_approval") is True
    assert result.get("interrupt_id") == "int-1"
    # Finalization fields MUST NOT be populated
    assert "manifest" not in result
    assert "e2_passed" not in result
    assert "agent_outputs" not in result
    # And no DB/state writes
    runner._update_results_tsv.assert_not_called()
    runner.study_store.append_round.assert_not_called()
    runner._run_review_cycle.assert_not_called()


def test_langgraph_maps_to_finalization_fields(env, ws):
    """Normal langgraph result must populate the finalization fields."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result(verdict="keep",
                                 metrics={"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.1})

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # Core finalization fields populated from langgraph result
    assert result["verdict"] == "keep"
    assert result["metrics"] == lg_result["metrics"]
    assert result["backtest_error"] is None

    # The mapped agent_outputs dict contains "researcher" (phases shape,
    # NOT "researcher_output") plus the _output-suffixed keys from the
    # langgraph rebuild.
    aos = result["agent_outputs"]
    assert "researcher" in aos, "phases-shape key required for generate_run_summary"
    assert aos["researcher"]["hypothesis"] == "h"
    assert aos["strategist_output"]["action"] == "discover_local"
    assert aos["data_quality_output"]["passed"] is True
    assert aos["risk_controller_output"]["risk_rating"] == "Green"

    # e2_passed reflects the verdict + target check
    assert result["e2_passed"] is True
    # passed_now is a set[str] of metric names that met their targets
    assert "calmar" in result["passed_now"]

    # Manifest was built with mapped fields
    assert result["manifest"]["verdict"]["decision"] == "keep"
    assert result["manifest"]["hypothesis"]["text"] == "h"


def test_langgraph_discard_fails_e2(env, ws):
    """Discard verdict → e2_passed=False even when metrics meet target."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result(verdict="discard")

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # verdict=discard blocks E2 even with great metrics
    assert result["verdict"] == "discard"
    assert result["e2_passed"] is False


def test_langgraph_metrics_missing_fails_e2(env, ws):
    """Metrics dict missing the targeted name → e2_passed=False."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    # metrics missing 'calmar' → can't pass the target check
    lg_result = _langgraph_result(metrics={"sharpe": 0.4, "max_dd": -0.1})

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    assert result["verdict"] == "keep"
    assert result["e2_passed"] is False


def test_langgraph_no_targets_never_passes_e2(env, ws):
    """When the study has no metric_targets, e2_passed is always False."""
    study = _make_study_record(ws)
    study.metric_targets = []  # no targets configured
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result()

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    assert result["e2_passed"] is False


def test_langgraph_runs_finalization_side_effects(env, ws):
    """Verify the shared finalization actually runs (manifest, journal, etc.)."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result()

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # DB mirror: study_rounds row
    runner.study_store.append_round.assert_called_once()
    call = runner.study_store.append_round.call_args
    assert call.kwargs["metrics"] == lg_result["metrics"]
    assert call.kwargs["verdict"] == lg_result["verdict"]

    # results.tsv update
    runner._update_results_tsv.assert_called_once()

    # Topology SSE event
    runner._emit_topology.assert_called_once()

    # SSE study_phase events: start/end for each phase step + exec phases
    # (less strict assertion — just check some were emitted)
    assert runner._emit.call_count > 0

    # Manifest saved (manifest.json + summary.md written under round_dir)
    assert (ws / "study" / "study-st-1" / "rounds" / "round_0001"
            / "manifest.json").exists()
    assert (ws / "study" / "study-st-1" / "rounds" / "round_0001"
            / "summary.md").exists()

    # State.json updated with last_completed_round
    from strategy_research.core.study import state_store as ss
    state = ss.load(ws, "study-st-1")
    assert state.last_completed_round == 1


def test_langgraph_researcher_hypothesis_extracts_correctly(env, ws):
    """hypothesis comes from researcher_output, NOT researcher_output directly."""
    study = _make_study_record(ws)
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result(researcher_hyp="custom hypothesis text")

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # hypothesis is normalized to {text, levers, predicted_affected}
    assert result["manifest"]["hypothesis"]["text"] == "custom hypothesis text"