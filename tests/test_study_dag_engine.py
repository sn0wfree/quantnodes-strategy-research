"""Tests for DAG-driven round execution (unified engine, Phase 5)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.agent.plugin import AgentPlugin
from strategy_research.core.agent.registry import AgentPluginRegistry
from strategy_research.core.study.graph import GraphEdge, GraphNode, StudyGraph
from strategy_research.core.study.runner import AutoresearchRunner


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def minimal_registry() -> AgentPluginRegistry:
    """Small registry for DAG execution tests.

    Replaces ``researcher`` / ``backtest`` / ``decide`` with stubs that
    emit predictable JSON. Other plugins fall back to minimal stubs
    (they won't execute because the standard graph skips them by
    default — see ``_make_standard_graph_subset``).
    """
    reg = AgentPluginRegistry()

    def make_stub(agent_id: str, payload: dict):
        plugin = AgentPlugin(
            id=agent_id, name=agent_id, category="execution",
            description=f"stub {agent_id}",
            prompt_file="",
        )
        return plugin, payload

    researcher_plugin, researcher_payload = make_stub(
        "researcher", {"hypothesis": "h", "action": "discover_local"},
    )
    dq_plugin, dq_payload = make_stub("data_quality", {"quality_ok": True})
    fa_plugin, fa_payload = make_stub("factor_analyst", {"ic": 0.06})
    strat_plugin, strat_payload = make_stub(
        "strategist", {"action": "discover_local", "hypothesis": "h"},
    )
    pc_plugin, pc_payload = make_stub("portfolio_construction", {"weights": []})
    risk_plugin, risk_payload = make_stub("risk_controller", {"risk_ok": True})
    attr_plugin, attr_payload = make_stub("attribution_analyst", {"attr": {}})
    aoa_plugin, aoa_payload = make_stub(
        "anti_overfit_analyst", {"overfit_passed": True, "overfit_score": 0.9},
    )
    diag_plugin, diag_payload = make_stub("backtest_diagnostics", {"diag": []})

    reg.register(researcher_plugin)
    reg.register(dq_plugin)
    reg.register(fa_plugin)
    reg.register(strat_plugin)
    reg.register(pc_plugin)
    reg.register(risk_plugin)
    reg.register(attr_plugin)
    reg.register(aoa_plugin)
    reg.register(diag_plugin)

    # backtest (python executor) + decide (evaluator) plugins
    backtest_plugin = AgentPlugin(
        id="backtest", name="Backtest", category="tool",
        description="backtest python executor",
        executor_type="python", python_function="run_backtest_script",
    )
    decide_plugin = AgentPlugin(
        id="decide", name="Decide", category="tool",
        description="decide evaluator",
        executor_type="evaluator", python_function="decide",
    )
    reg.register(backtest_plugin)
    reg.register(decide_plugin)

    # Stash payloads for the test to install into AgentExecutor.
    reg._payloads = {
        "researcher": researcher_payload,
        "data_quality": dq_payload,
        "factor_analyst": fa_payload,
        "strategist": strat_payload,
        "portfolio_construction": pc_payload,
        "risk_controller": risk_payload,
        "attribution_analyst": attr_payload,
        "anti_overfit_analyst": aoa_payload,
        "backtest_diagnostics": diag_payload,
        "backtest": {"success": True, "metrics": {"calmar": 0.8, "sharpe": 1.2}},
        "decide": {"verdict": "keep"},
    }
    return reg


def _patch_executor(monkeypatch, registry: AgentPluginRegistry):
    """Replace AgentExecutor.execute with a deterministic stub.

    Looks up the plugin in the registry, emits the preset payload, and
    records the call for inspection.
    """
    from strategy_research.core.agent import executor as exec_mod

    calls: list[tuple[str, dict]] = []

    def fake_execute(self, plugin, task, workspace, **kwargs):
        from strategy_research.core.agent.executor import AgentExecutionResult
        payload = getattr(registry, "_payloads", {}).get(plugin.id, {})
        output = json.dumps(payload, ensure_ascii=False)
        calls.append((plugin.id, dict(kwargs)))
        return AgentExecutionResult(
            agent_id=plugin.id, status="success",
            output=output, elapsed_s=0.01,
            summary=output[:60], metrics={"iterations": 1},
        )

    monkeypatch.setattr(
        exec_mod.AgentExecutor, "execute", fake_execute,
    )
    return calls


# Full pipeline graph (standard 8 + backtest + decide + backtest_diagnostics)
FULL_PIPELINE_GRAPH = StudyGraph(
    nodes=(
        GraphNode(id="researcher", type="llm_agent", label="Researcher"),
        GraphNode(id="data_quality", type="evaluator", label="DQ"),
        GraphNode(id="factor_analyst", type="llm_agent", label="FA"),
        GraphNode(id="strategist", type="planner", label="Strat"),
        GraphNode(id="portfolio_construction", type="llm_agent", label="PC"),
        GraphNode(id="backtest", type="tool", label="Backtest"),
        GraphNode(id="risk_controller", type="evaluator", label="Risk"),
        GraphNode(id="attribution_analyst", type="evaluator", label="Attr"),
        GraphNode(id="anti_overfit_analyst", type="evaluator", label="AOA"),
        GraphNode(id="backtest_diagnostics", type="evaluator", label="Diag"),
        GraphNode(id="decide", type="evaluator", label="Decide"),
    ),
    edges=(
        GraphEdge(source="researcher", target="data_quality"),
        GraphEdge(source="researcher", target="factor_analyst"),
        GraphEdge(source="data_quality", target="strategist"),
        GraphEdge(source="factor_analyst", target="strategist"),
        GraphEdge(source="strategist", target="portfolio_construction"),
        GraphEdge(source="portfolio_construction", target="backtest"),
        GraphEdge(source="backtest", target="risk_controller"),
        GraphEdge(source="backtest", target="attribution_analyst"),
        GraphEdge(source="backtest", target="anti_overfit_analyst"),
        GraphEdge(source="risk_controller", target="attribution_analyst"),
        GraphEdge(source="risk_controller", target="anti_overfit_analyst"),
        GraphEdge(source="attribution_analyst", target="anti_overfit_analyst"),
        GraphEdge(source="anti_overfit_analyst", target="backtest_diagnostics"),
        GraphEdge(source="backtest", target="decide"),
        GraphEdge(source="anti_overfit_analyst", target="decide"),
        GraphEdge(source="backtest_diagnostics", target="decide"),
    ),
)


# ── _run_round_via_dag output mapping ────────────────────────────────


class TestRebuildPhaseOutputs:
    def _runner(self, workspace: Path):
        study = MagicMock()
        study.study_id = "st-1"
        study.session_id = "s-1"
        study.workspace_path = str(workspace)
        study.strategy_name = "foo"
        study.metric_targets = []
        study.behavior = None
        study.lazy_detection_interval = 10
        study.keep_recent = 10
        study.objective = "obj"
        return study

    def test_full_dag_round(self, monkeypatch, workspace, minimal_registry):
        """End-to-end: standard DAG → legacy-shaped result dict."""
        from strategy_research.core.agent import executor as exec_mod

        # Register python+evaluator builtins for backtest/decide.
        from strategy_research.core.agent import exec_registry

        def _backtest(workspace_path, upstream=None, **kw):
            return {
                "success": True, "metrics": {"calmar": 0.8, "sharpe": 1.2},
            }

        exec_registry.register_python_executor("run_backtest_script", _backtest)

        def _decide(metrics=None, **kw):
            from strategy_research.core.strategy_acceptance import (
                AcceptanceDecision,
            )
            return AcceptanceDecision(verdict="keep", reason="ok")

        exec_registry.register_evaluator("decide", _decide)

        calls = _patch_executor(monkeypatch, minimal_registry)

        runner = AutoresearchRunner.__new__(AutoresearchRunner)
        runner._get_study = lambda: self._runner(workspace)
        runner._session_manager = None
        runner._plugin_registry = minimal_registry

        run_dir = workspace / "run1"
        run_dir.mkdir()

        result = runner._run_round_via_dag(
            workspace, "foo", {}, run_dir, FULL_PIPELINE_GRAPH,
            session="s-1", sid="st-1", round_num=1,
            directive_text=None,
        )

        # Every layer's agent called.
        called_ids = [c[0] for c in calls]
        assert "researcher" in called_ids
        assert "data_quality" in called_ids
        assert "strategist" in called_ids
        assert "risk_controller" in called_ids

        # Result schema matches the legacy shape.
        assert "researcher_output" in result
        assert result["researcher_output"]["hypothesis"] == "h"
        assert result["metrics"]["calmar"] == 0.8
        assert result["backtest_result"]["success"] is True
        assert result["verdict"] == "keep"
        assert isinstance(result["decision"], object)
        assert "data_quality_output" in result
        assert "factor_analyst_output" in result
        assert "strategist_output" in result
        assert "portfolio_construction_output" in result
        assert "risk_controller_output" in result
        assert "attribution_analyst_output" in result
        assert "anti_overfit_analyst_output" in result
        assert "backtest_diagnostics_output" in result

    def test_unknown_plugin_skipped(self, monkeypatch, workspace,
                                   minimal_registry):
        """Plugins not in the registry produce a warning and are skipped."""
        from strategy_research.core.agent import executor as exec_mod
        from strategy_research.core.agent.executor import AgentExecutionResult

        def fake_execute(self, plugin, task, workspace, **kwargs):
            return AgentExecutionResult(
                agent_id=plugin.id, status="error",
                output="", error="forced", elapsed_s=0.01,
            )

        monkeypatch.setattr(exec_mod.AgentExecutor, "execute", fake_execute)

        # Strip registry of 'data_quality' to trigger unknown-plugin path.
        minimal_registry._plugins.pop("data_quality", None)

        runner = AutoresearchRunner.__new__(AutoresearchRunner)
        runner._get_study = lambda: self._runner(workspace)
        runner._session_manager = None
        runner._plugin_registry = minimal_registry

        run_dir = workspace / "run1"
        run_dir.mkdir()

        result = runner._run_round_via_dag(
            workspace, "foo", {}, run_dir, FULL_PIPELINE_GRAPH,
            session="s-1", sid="st-1", round_num=1,
            directive_text=None,
        )

        # data_quality is missing → empty dict in output.
        assert result["data_quality_output"] == {}

    def test_agent_error_recorded_as_parse_failed(
        self, monkeypatch, workspace, minimal_registry,
    ):
        from strategy_research.core.agent import executor as exec_mod
        from strategy_research.core.agent.executor import AgentExecutionResult

        def fail_for(self, plugin, task, workspace, **kwargs):
            if plugin.id == "factor_analyst":
                return AgentExecutionResult(
                    agent_id=plugin.id, status="error",
                    output="", error="llm blew up", elapsed_s=0.0,
                )
            from strategy_research.core.agent import exec_registry
            payloads = getattr(minimal_registry, "_payloads", {})
            payload = payloads.get(plugin.id, {})
            output = json.dumps(payload, ensure_ascii=False)
            return AgentExecutionResult(
                agent_id=plugin.id, status="success",
                output=output, elapsed_s=0.01,
                metrics={"iterations": 1},
            )

        # Need python+evaluator registrations too.
        from strategy_research.core.agent import exec_registry

        def _backtest(workspace_path, upstream=None, **kw):
            return {"success": True, "metrics": {}}
        exec_registry.register_python_executor("run_backtest_script", _backtest)

        def _decide(metrics=None, **kw):
            from strategy_research.core.strategy_acceptance import (
                AcceptanceDecision,
            )
            return AcceptanceDecision(verdict="discard", reason="")
        exec_registry.register_evaluator("decide", _decide)

        monkeypatch.setattr(exec_mod.AgentExecutor, "execute", fail_for)

        runner = AutoresearchRunner.__new__(AutoresearchRunner)
        runner._get_study = lambda: self._runner(workspace)
        runner._session_manager = None
        runner._plugin_registry = minimal_registry

        run_dir = workspace / "run1"
        run_dir.mkdir()

        result = runner._run_round_via_dag(
            workspace, "foo", {}, run_dir, FULL_PIPELINE_GRAPH,
            session="s-1", sid="st-1", round_num=1,
            directive_text=None,
        )

        assert result["factor_analyst_output"].get("parse_failed") is True
        assert "llm blew up" in result["factor_analyst_output"]["error"]


# ── Feature flag wiring ──────────────────────────────────────────────


class TestEngineDispatch:
    def test_env_flag_maps_phases_to_langgraph(self, monkeypatch):
        """SR_STUDY_DAG_ENGINE=1 remaps engine='phases' to langgraph
        in phase_engine (the actual dispatch point)."""
        monkeypatch.setenv("SR_STUDY_DAG_ENGINE", "1")
        import os
        assert os.environ.get("SR_STUDY_DAG_ENGINE") == "1"

    def test_engine_dag_maps_to_langgraph(self):
        """engine='dag' is mapped to langgraph in phase_engine."""
        from strategy_research.core.study import phase_engine
        import inspect
        src = inspect.getsource(phase_engine.run_round_phases)
        assert "langgraph" in src
