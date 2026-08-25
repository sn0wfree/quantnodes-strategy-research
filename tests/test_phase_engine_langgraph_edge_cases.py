"""Edge-case tests for run_round_phases langgraph branch.

These supplement test_phase_engine_langgraph_branch.py with scenarios
that are less common but still important for correctness:

- Langgraph result with no agent_outputs at all
- Langgraph result whose verdict is 'discard' (forces e2_passed=False)
- Langgraph result with guidance gate violations
- Langgraph result where metrics dict is empty / missing the target
- Langgraph result with only researcher_output (partial agent set)
- paused_for_approval result preserves all langgraph keys
- e2_passed is False when verdict is keep but gate violations exist
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.study.phase_engine import run_round_phases
from strategy_research.core.study.runner import AutoresearchRunner


# ── Shared fixtures (mirror test_phase_engine_langgraph_branch.py) ──


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    ws = tmp_path
    (ws / "strategies" / "demo").mkdir(parents=True, exist_ok=True)
    (ws / "strategies" / "demo" / "strategy.py").write_text(
        "PARAMS = {}\n", encoding="utf-8"
    )
    return ws


def _study_record(ws, **overrides):
    rec = MagicMock()
    rec.study_id = "study-st-1"
    rec.session_id = "study-st-1"
    rec.workspace_path = str(ws)
    rec.strategy_name = "demo"
    rec.engine = "langgraph"
    rec.executor_type = "autoresearch"
    rec.behavior = None
    rec.metric_targets = overrides.get(
        "metric_targets",
        [{"name": "calmar", "op": ">=", "value": 0.5}],
    )
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
    rec.objective = overrides.get("objective", "test objective")
    rec.archived_at = None
    return rec


def _runner(ws):
    runner = MagicMock()
    runner._get_study.return_value = _study_record(ws)
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
    runner._goal_store = MagicMock()
    runner._scoreboard = MagicMock()
    runner.study_store = MagicMock()
    return runner


def _paused_result():
    return {
        "round": 1,
        "run_name": "round_1",
        "paused_for_approval": True,
        "study_id": "study-st-1",
        "interrupt_id": "int-abc",
    }


def _normal_result(**overrides):
    """Minimal valid langgraph result with all expected keys."""
    from strategy_research.core.strategy_acceptance import (
        AcceptanceDecision, decide as sa_decide,
    )
    metrics = overrides.get("metrics", {"calmar": 0.6, "sharpe": 0.4})
    verdict = overrides.get("verdict", "keep")
    try:
        decision = sa_decide(metrics=metrics, llm_verdict=None)
    except Exception:
        decision = AcceptanceDecision(verdict=verdict, reason="")
    return {
        "researcher_output": overrides.get(
            "researcher_output",
            {"hypothesis": "h", "action": "discover_local"},
        ),
        "data_quality_output": overrides.get("data_quality_output", {"passed": True}),
        "factor_analyst_output": overrides.get("factor_analyst_output", {}),
        "strategist_output": overrides.get(
            "strategist_output",
            {"action": "discover_local", "changes": []},
        ),
        "portfolio_construction_output": overrides.get("portfolio_construction_output", {}),
        "backtest_result": {"metrics": metrics, "success": True},
        "backtest_error": None,
        "metrics": metrics,
        "risk_controller_output": overrides.get("risk_controller_output", {}),
        "attribution_analyst_output": overrides.get("attribution_analyst_output", {}),
        "anti_overfit_analyst_output": overrides.get(
            "anti_overfit_analyst_output", {"verdict": "keep"},
        ),
        "backtest_diagnostics_output": {},
        "decision": decision,
        "verdict": verdict,
        "aoa_llm_verdict": {},
        "round": 1,
        "run_name": "round_1",
    }


# ── Tests ───────────────────────────────────────────────────


def test_paused_preserves_all_langgraph_keys(env):
    """Early-return result must include every key langgraph set."""
    runner = _runner(env)
    paused = {
        "round": 5,
        "run_name": "round_5",
        "paused_for_approval": True,
        "study_id": "study-st-1",
        "interrupt_id": "int-xyz-123",
        "hypothesis": "agent wants to try something",
    }

    with patch.object(runner, "_run_round_via_langgraph", return_value=paused):
        result = run_round_phases(
            runner, round_num=5, previous_summary=None, directive_text=None
        )

    for k, v in paused.items():
        assert result.get(k) == v, f"key {k!r} not preserved"


def test_no_agent_outputs_at_all(env):
    """langgraph returns {} (no agents ran) → verdict=discard, e2_passed=False."""
    runner = _runner(env)
    # Build a result where only metrics is set; agent_outputs is empty
    from strategy_research.core.strategy_acceptance import (
        AcceptanceDecision, decide as sa_decide,
    )
    metrics = {"calmar": 0.6}
    decision = sa_decide(metrics=metrics, llm_verdict=None)
    empty = {
        "researcher_output": {},
        "data_quality_output": {},
        "factor_analyst_output": {},
        "strategist_output": {},
        "portfolio_construction_output": {},
        "backtest_result": {"metrics": metrics, "success": True},
        "backtest_error": None,
        "metrics": metrics,
        "risk_controller_output": {},
        "attribution_analyst_output": {},
        "anti_overfit_analyst_output": {},
        "backtest_diagnostics_output": {},
        "decision": decision,
        "verdict": "discard",  # forced because no anti-overfit signal
        "aoa_llm_verdict": {},
        "round": 1,
        "run_name": "round_1",
    }

    with patch.object(runner, "_run_round_via_langgraph", return_value=empty):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    assert result["verdict"] == "discard"
    assert result["e2_passed"] is False
    # agent_outputs dict still has the researcher key (even if empty)
    assert "researcher" in result["agent_outputs"]
    assert result["agent_outputs"]["researcher"] == {}


def test_guidance_gate_violation_forces_discard(env):
    """Guidance gates can downgrade verdict=keep to discard, blocking e2."""
    from strategy_research.core.study import guidance as gd_mod

    runner = _runner(env)

    # Build a fake guidance object with one violating gate.
    fake_gate = MagicMock()
    fake_gate.id = "g1" if hasattr(fake_gate, "id") else "g1"
    # Guidance.gates is iterated by check_violations; gate.dict may be read.
    fake_gate.id = "g1"
    # mock_violations returns the "found" tuple from check_violations
    fake_guidance = MagicMock()
    fake_guidance.gates = [fake_gate]

    # Patch both load_guidance (returns our fake guidance) and
    # check_violations (returns a violation for the gate).
    # Also stub render_guidance_section to skip rendering (the
    # fake gate is a MagicMock without real fields).
    with patch.object(gd_mod, "load_guidance", return_value=fake_guidance), \
         patch.object(gd_mod, "render_guidance_section", return_value=""), \
         patch.object(gd_mod, "check_violations",
                      return_value=([{"id": "g1", "metric": "calmar",
                                     "op": ">=", "value": 1.0}], [])):
        lg_result = _normal_result(verdict="keep", metrics={"calmar": 0.6})
        with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
            result = run_round_phases(
                runner, round_num=1, previous_summary=None, directive_text=None
            )

    # Despite verdict=keep from langgraph, gates forced discard
    assert result["verdict"] == "discard"
    assert result["e2_passed"] is False
    # Gate info surfaced in manifest
    assert result["manifest"]["gates"] is not None


def test_metrics_empty_dict_blocks_e2(env):
    """metrics={} → all targets fail → e2_passed=False."""
    runner = _runner(env)
    lg_result = _normal_result(verdict="keep", metrics={})

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    assert result["verdict"] == "keep"  # verdict stays keep (no gates)
    assert result["e2_passed"] is False  # but can't pass with no metrics
    assert result["passed_now"] == set()  # no metric met its target


def test_metrics_missing_target_key_blocks_e2(env):
    """Metrics has sharpe/max_dd but no calmar → can't meet calmar target."""
    runner = _runner(env)
    lg_result = _normal_result(
        verdict="keep", metrics={"sharpe": 0.4, "max_dd": -0.1},
    )

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # calmar not in metrics → target can't be checked → passed_now doesn't include calmar
    assert "calmar" not in result["passed_now"]
    assert result["e2_passed"] is False


def test_only_researcher_output_present(env):
    """langgraph returns only researcher_output (no other agents)."""
    from strategy_research.core.strategy_acceptance import (
        AcceptanceDecision, decide as sa_decide,
    )
    runner = _runner(env)
    metrics = {"calmar": 0.6}
    try:
        decision = sa_decide(metrics=metrics, llm_verdict=None)
    except Exception:
        decision = AcceptanceDecision(verdict="keep", reason="")
    lg_result = {
        "researcher_output": {"hypothesis": "h"},
        "data_quality_output": {},
        "factor_analyst_output": {},
        "strategist_output": {},
        "portfolio_construction_output": {},
        "backtest_result": {"metrics": metrics, "success": True},
        "backtest_error": None,
        "metrics": metrics,
        "risk_controller_output": {},
        "attribution_analyst_output": {},
        "anti_overfit_analyst_output": {},
        "backtest_diagnostics_output": {},
        "decision": decision,
        "verdict": "keep",
        "aoa_llm_verdict": {},
        "round": 1,
        "run_name": "round_1",
    }

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # researcher key has the actual hypothesis
    assert result["agent_outputs"]["researcher"] == {"hypothesis": "h"}
    # other _output keys exist (they were in lg_result) but hold empty dicts
    for k in ("data_quality_output", "factor_analyst_output",
              "strategist_output", "portfolio_construction_output",
              "risk_controller_output", "attribution_analyst_output",
              "anti_overfit_analyst_output", "backtest_diagnostics_output"):
        assert k in result["agent_outputs"]
        assert result["agent_outputs"][k] == {}


def test_paused_returned_to_runner_loop_with_interrupt_id(env):
    """paused_for_approval result carries enough info for the runner loop to poll."""
    runner = _runner(env)
    paused = _paused_result()

    with patch.object(runner, "_run_round_via_langgraph", return_value=paused):
        result = run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # The runner loop uses these fields to start the HITL poll:
    #   - paused_for_approval=True
    #   - interrupt_id (from SSE event payload → frontend uses real id)
    assert result["paused_for_approval"] is True
    assert result["interrupt_id"] == "int-abc"
    assert result["study_id"] == "study-st-1"
    assert result["round"] == 1

    # None of the finalization fields were populated
    assert "manifest" not in result
    assert "e2_passed" not in result
    assert "agent_outputs" not in result