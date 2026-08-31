"""Archived from tests/test_workflow_e2e.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


    @pytest.mark.skip(reason="P8 cleanup: WorkflowController/ControllerConfig removed")
    def test_full_workflow_single_round(self):
        reg = AgentRegistry()
        reg.register(StubExecutor("researcher", {"action": "tweak_factor", "hypothesis": "momentum works"}))
        reg.register(StubExecutor("data_quality", {"completeness": 0.95}))
        reg.register(StubExecutor("factor_analyst", {"ic_mean": 0.05, "ir_mean": 1.2}))
        reg.register(StubExecutor("strategist", {"changes": {"lookback": 20}}))
        reg.register(StubExecutor("portfolio_construction", {"weights": {"momentum": 0.6, "value": 0.4}}))
        reg.register(StubExecutor("risk_controller", {"verdict": "pass", "max_drawdown": -0.15}))
        reg.register(StubExecutor("attribution_analyst", {"sources": {"momentum": 0.7, "value": 0.3}}))
        reg.register(StubExecutor("anti_overfit_analyst", {"methods_passed": 4, "methods_total": 6}))
        reg.register(StubExecutor("backtest_diagnostics", {"metrics": {"sharpe": 0.8, "calmar": 0.6, "max_dd": -0.15}}))

        # deps convention: {node: [upstream_deps]}
        adj = {
            "researcher": [],
            "data_quality": ["researcher"],
            "factor_analyst": ["researcher"],
            "strategist": ["data_quality", "factor_analyst"],
            "portfolio_construction": ["strategist"],
            "risk_controller": ["portfolio_construction"],
            "attribution_analyst": ["risk_controller"],
            "anti_overfit_analyst": ["attribution_analyst"],
            "backtest_diagnostics": ["anti_overfit_analyst"],
        }

        validate_dag(adj)
        config = ControllerConfig(max_retries=1, retry_delay=0.0)
        ctrl = WorkflowController(reg, adj, config)

        assert len(ctrl.layers) == 8

        round_result = ctrl.execute_round(1, "Research momentum factor")
        assert round_result.round_num == 1
        assert len(round_result.executions) == 9

        success_count = sum(1 for e in round_result.executions if e.status == AgentStatus.SUCCESS)
        assert success_count == 9


    @pytest.mark.skip(reason="P8 cleanup: WorkflowController/ControllerConfig removed")
    def test_workflow_with_failed_agent(self):
        reg = AgentRegistry()
        reg.register(StubExecutor("a"))
        reg.register(FailingExecutor("b"))
        # deps convention: b depends on a
        adj = {"a": [], "b": ["a"]}
        config = ControllerConfig(max_retries=1, retry_delay=0.0)
        ctrl = WorkflowController(reg, adj, config)

        result = ctrl.execute_round(1, "test")
        assert result.executions[0].status == AgentStatus.SUCCESS
        assert result.executions[1].status == AgentStatus.ERROR


    @pytest.mark.skip(reason="P8 cleanup: PromptBuilder removed")
    def test_prompt_builder_with_templates(self, tmp_path):
        prompt_file = tmp_path / "researcher.md"
        prompt_file.write_text("# Researcher\nAnalyze the strategy.")

        builder = PromptBuilder(tmp_path)
        prompt = builder.build_prompt("researcher", base_prompt="Focus on momentum")
        assert "# Researcher" in prompt
        assert "Focus on momentum" in prompt


    @pytest.mark.skip(reason="P8 cleanup: WorkflowController removed")
    def test_round_execution_time(self):
        reg = AgentRegistry()
        reg.register(StubExecutor("a"))
        adj = {"a": []}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "test")
        assert result.total_duration_ms >= 0
