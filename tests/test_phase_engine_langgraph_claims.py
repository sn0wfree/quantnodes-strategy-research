"""Step 0: claims must flow from phase-A capture to phase-B journal backfill.

phase_engine.run_round_phases writes the journal in two phases (see
_capture_round_claims docstring):

- phase A: ``_capture_round_claims`` appends the entry carrying the
  researcher's ``predictions`` (captured BEFORE the backtest, so they
  stay falsifiable) — line 190 on the langgraph branch, line 238 on
  the phases branch.
- phase B: ``_record_journal_and_regression`` (shared finalization,
  line 317-320) backfills attribution / levers / outcomes, receiving
  ``predictions=captured_predictions``.

On the langgraph branch, line 205 resets ``captured_predictions = {}``
after phase A captured real values — so phase B would receive an empty
dict while phases passes the real one. This test pins:

    phase-B predictions == phase-A capture output   (langgraph branch)

Existing parity tests only assert output schema, not journal
side-effects, which is why this path was never covered.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.study.phase_engine import run_round_phases

# ── Fixtures (mirror test_phase_engine_langgraph_branch.py) ──


@pytest.fixture
def ws(tmp_path: Path) -> Path:
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


def _make_study_record(ws: Path, *, engine: str = "langgraph"):
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
    runner._goal_store = MagicMock()
    runner._scoreboard = MagicMock()
    runner.study_store = MagicMock()
    return runner


def _langgraph_result():
    from strategy_research.core.strategy_acceptance import (
        AcceptanceDecision,
    )
    from strategy_research.core.strategy_acceptance import (
        decide as sa_decide,
    )

    metrics = {"calmar": 0.7, "sharpe": 0.4, "max_dd": -0.1}
    try:
        decision = sa_decide(metrics=metrics, llm_verdict=None)
    except Exception:  # noqa: BLE001
        decision = AcceptanceDecision(verdict="keep", reason="")
    return {
        "researcher_output": {"hypothesis": "h", "action": "discover_local"},
        "data_quality_output": {"passed": True},
        "factor_analyst_output": {"recommendation": "ok"},
        "strategist_output": {
            "action": "discover_local",
            "changes": [{"param": "top_n", "old": 10, "new": 20}],
        },
        "portfolio_construction_output": {"method": "equal"},
        "backtest_result": {"metrics": metrics, "success": True},
        "backtest_error": None,
        "metrics": metrics,
        "risk_controller_output": {"risk_passed": True, "risk_rating": "Green"},
        "attribution_analyst_output": {"alpha": 0.02},
        "anti_overfit_analyst_output": {"verdict": "keep"},
        "backtest_diagnostics_output": {},
        "decision": decision,
        "verdict": "keep",
        "aoa_llm_verdict": {},
        "round": 1,
        "run_name": "round_1",
    }


# ── Test ─────────────────────────────────────────────────────


def test_langgraph_branch_passes_captured_claims_to_journal_backfill(env, ws):
    """phase B must receive exactly what phase A captured (langgraph branch).

    Failure mode this guards: phase_engine.py resets captured_predictions
    to {} between capture and backfill on the langgraph branch only.
    """
    study = _make_study_record(ws, engine="langgraph")
    runner = _make_runner(ws, study)
    lg_result = _langgraph_result()

    # Phase-A sentinel: pretend normalize/append produced these claims.
    phase_a_claims = {"calmar": 0.6, "sharpe": 0.35}

    with patch.object(runner, "_run_round_via_langgraph", return_value=lg_result), \
        patch(
            "strategy_research.core.study.phase_engine._capture_round_claims",
            return_value=phase_a_claims,
        ) as cap_mock, \
        patch(
            "strategy_research.core.study.phase_engine._record_journal_and_regression",
        ) as journal_mock:
        run_round_phases(
            runner, round_num=1, previous_summary=None, directive_text=None
        )

    # Phase A ran and produced the sentinel (sanity: setup reached line 190).
    assert cap_mock.called, "phase A capture never ran — setup is broken"
    # Phase B must carry the SAME claims forward (the actual contract).
    assert journal_mock.called, "phase B journal backfill never ran"
    phase_b_claims = journal_mock.call_args.kwargs.get("predictions")
    assert phase_b_claims == phase_a_claims, (
        "langgraph branch lost captured claims between phase A and phase B: "
        f"phase A produced {phase_a_claims!r}, phase B received {phase_b_claims!r}"
    )
