"""Unit tests for the DAG engine helper methods on AutoresearchRunner.

Covers:
- _try_parse_json: JSON parsing + fallback for non-string / non-JSON
- _build_round_task_text: section composition
- _rebuild_phase_outputs: legacy schema translation (researcher,
  backtest, decide) with backtest-as-string and missing-node branches

These are pure functions / pure data transforms — exercised via
``AutoresearchRunner.__new__`` to bypass the heavyweight __init__.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from strategy_research.core.study.runner import AutoresearchRunner


def _bare_runner() -> AutoresearchRunner:
    """Build a runner without running __init__ (no store, no scheduler)."""
    return AutoresearchRunner.__new__(AutoresearchRunner)


# ── _try_parse_json ───────────────────────────────────────────────────


class TestTryParseJson:
    def test_parses_valid_json_object(self):
        runner = _bare_runner()
        assert runner._try_parse_json('{"a": 1}') == {"a": 1}

    def test_parses_valid_json_array(self):
        runner = _bare_runner()
        assert runner._try_parse_json("[1, 2, 3]") == [1, 2, 3]

    def test_parses_json_string(self):
        runner = _bare_runner()
        assert runner._try_parse_json('"hello"') == "hello"

    def test_parses_json_number(self):
        runner = _bare_runner()
        assert runner._try_parse_json("42") == 42

    def test_invalid_json_returns_raw(self):
        runner = _bare_runner()
        assert runner._try_parse_json("not json") == "not json"

    def test_empty_string_returns_empty(self):
        runner = _bare_runner()
        assert runner._try_parse_json("") == ""

    def test_non_string_returns_unchanged(self):
        runner = _bare_runner()
        # Non-string input (e.g. dict already parsed) returns as-is.
        assert runner._try_parse_json({"a": 1}) == {"a": 1}
        assert runner._try_parse_json(None) is None
        assert runner._try_parse_json(42) == 42


# ── _build_round_task_text ────────────────────────────────────────────


class TestBuildRoundTaskText:
    def test_no_state_no_directive_only_intro(self):
        runner = _bare_runner()
        out = runner._build_round_task_text({}, None)
        assert out == "根据研究目标与历史结果完成当前轮次的工作。"

    def test_with_state_includes_current_state_section(self):
        runner = _bare_runner()
        state = {"strategy_name": "foo", "round": 3}
        out = runner._build_round_task_text(state, None)
        assert "## 当前状态" in out
        assert '"strategy_name": "foo"' in out

    def test_with_directive_includes_directive_section(self):
        runner = _bare_runner()
        out = runner._build_round_task_text({}, "请优化因子权重")
        assert "## 用户指令" in out
        assert "请优化因子权重" in out

    def test_full_sections_appear_in_order(self):
        runner = _bare_runner()
        out = runner._build_round_task_text(
            {"k": "v"},
            "请继续",
        )
        intro = out.index("根据研究目标")
        state = out.index("## 当前状态")
        directive = out.index("## 用户指令")
        assert intro < state < directive

    def test_non_serializable_state_uses_default_str(self):
        runner = _bare_runner()
        state = {"data": object()}
        # Should not raise — json.dumps with default=str.
        out = runner._build_round_task_text(state, None)
        assert "## 当前状态" in out


# ── _rebuild_phase_outputs ───────────────────────────────────────────


def _agent_execution_result(
    output: str, *, status: str = "success", elapsed: float = 0.1,
    metrics: dict | None = None,
) -> Any:
    """Build an AgentExecutionResult-shaped duck-type."""
    from strategy_research.core.agent.executor import AgentExecutionResult
    return AgentExecutionResult(
        agent_id="x", status=status, output=output,
        elapsed_s=elapsed, metrics=metrics or {},
    )


class TestRebuildPhaseOutputs:
    def test_minimal_agent_outputs_produce_empty_metrics(self):
        runner = _bare_runner()
        result = runner._rebuild_phase_outputs({}, graph=None)
        assert result["researcher_output"] == {}
        assert result["metrics"] == {}
        assert result["backtest_result"] == {}
        assert result["backtest_error"] is None
        # verdict falls back to "discard"
        assert result["verdict"] == "discard"

    def test_researcher_output_propagates(self):
        runner = _bare_runner()
        outputs = {"researcher": {"hypothesis": "h", "action": "discover"}}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["researcher_output"]["hypothesis"] == "h"

    def test_backtest_dict_extracts_metrics(self):
        runner = _bare_runner()
        outputs = {
            "backtest": {"success": True,
                         "metrics": {"calmar": 0.8, "sharpe": 1.2}},
        }
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["metrics"] == {"calmar": 0.8, "sharpe": 1.2}
        assert result["backtest_result"]["success"] is True
        assert result["backtest_error"] is None

    def test_backtest_json_string_is_parsed(self):
        runner = _bare_runner()
        outputs = {"backtest": '{"success": true, "metrics": {"calmar": 1.0}}'}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["metrics"] == {"calmar": 1.0}

    def test_backtest_invalid_json_yields_empty(self):
        runner = _bare_runner()
        outputs = {"backtest": "not json at all"}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["metrics"] == {}
        assert result["backtest_result"] == {}

    def test_backtest_error_field_propagates_when_failed(self):
        runner = _bare_runner()
        outputs = {"backtest": {"success": False, "error": "boom"}}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["backtest_error"] == "boom"

    def test_backtest_error_none_when_succeeded(self):
        runner = _bare_runner()
        outputs = {"backtest": {"success": True}}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["backtest_error"] is None

    def test_decide_dict_propagates_verdict(self):
        runner = _bare_runner()
        outputs = {
            "decide": {"verdict": "keep", "reason": "ok"},
            "backtest": {"metrics": {"calmar": 0.5}},
        }
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["verdict"] == "keep"

    def test_decide_json_string_propagates_verdict(self):
        runner = _bare_runner()
        outputs = {
            "decide": '{"verdict": "discard"}',
            "backtest": {"metrics": {}},
        }
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["verdict"] == "discard"

    def test_decide_invalid_json_yields_discard_verdict(self):
        runner = _bare_runner()
        outputs = {"decide": "garbage"}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["verdict"] == "discard"

    def test_all_agent_slots_default_to_empty_dict(self):
        runner = _bare_runner()
        outputs = {"backtest": {"metrics": {}}}
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        for key in (
            "data_quality_output",
            "factor_analyst_output",
            "strategist_output",
            "portfolio_construction_output",
            "risk_controller_output",
            "attribution_analyst_output",
            "anti_overfit_analyst_output",
            "backtest_diagnostics_output",
        ):
            assert result[key] == {}, f"{key} should default to empty dict"

    def test_aoa_llm_verdict_is_anti_overfit(self):
        runner = _bare_runner()
        outputs = {
            "anti_overfit_analyst": {"overfit_passed": True},
            "backtest": {"metrics": {}},
        }
        result = runner._rebuild_phase_outputs(outputs, graph=None)
        assert result["aoa_llm_verdict"] == {"overfit_passed": True}


# ── decision object ──────────────────────────────────────────────────


class TestDecisionObject:
    def test_decision_object_has_to_dict(self):
        """Downstream code calls .to_dict() on the decision; verify shape."""
        from strategy_research.core.strategy_acceptance import AcceptanceDecision
        runner = _bare_runner()
        result = runner._rebuild_phase_outputs({}, graph=None)
        d = result["decision"]
        assert hasattr(d, "to_dict")
        d_dict = d.to_dict()
        assert isinstance(d_dict, dict)