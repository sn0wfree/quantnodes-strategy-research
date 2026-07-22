import pytest
from strategy_research.core.workflow import (
    AgentExecutor,
    AgentRegistry,
    AgentStatus,
    ControllerConfig,
    PromptBuilder,
    WorkflowController,
    topological_layers,
    validate_dag,
)
from strategy_research.core.workflow.executors import StubExecutor
from strategy_research.core.workflow.validator import AgentValidator


class FailingExecutor:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        raise RuntimeError(f"{self._name} failed")


class TestWorkflowE2E:
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

        adj = {
            "researcher": ["data_quality", "factor_analyst"],
            "data_quality": ["strategist"],
            "factor_analyst": ["strategist"],
            "strategist": ["portfolio_construction"],
            "portfolio_construction": ["risk_controller"],
            "risk_controller": ["attribution_analyst"],
            "attribution_analyst": ["anti_overfit_analyst"],
            "anti_overfit_analyst": ["backtest_diagnostics"],
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

    def test_workflow_with_failed_agent(self):
        reg = AgentRegistry()
        reg.register(StubExecutor("a"))
        reg.register(FailingExecutor("b"))
        adj = {"a": ["b"]}
        config = ControllerConfig(max_retries=1, retry_delay=0.0)
        ctrl = WorkflowController(reg, adj, config)

        result = ctrl.execute_round(1, "test")
        assert result.executions[0].status == AgentStatus.SUCCESS
        assert result.executions[1].status == AgentStatus.ERROR

    def test_prompt_builder_with_templates(self, tmp_path):
        prompt_file = tmp_path / "researcher.md"
        prompt_file.write_text("# Researcher\nAnalyze the strategy.")

        builder = PromptBuilder(tmp_path)
        prompt = builder.build_prompt("researcher", base_prompt="Focus on momentum")
        assert "# Researcher" in prompt
        assert "Focus on momentum" in prompt

    def test_validator_integration(self):
        validator = AgentValidator()

        output = {"action": "tweak_factor", "factor_direction": "positive"}
        result = validator.validate("researcher", output)
        assert result.valid is True

        output = {"action": "invalid"}
        result = validator.validate("researcher", output)
        assert result.valid is False

    def test_dag_layers_computation(self):
        adj = {
            "researcher": ["data_quality", "factor_analyst"],
            "data_quality": ["strategist"],
            "factor_analyst": ["strategist"],
            "strategist": ["portfolio_construction"],
            "portfolio_construction": ["risk_controller"],
            "risk_controller": ["attribution_analyst"],
            "attribution_analyst": ["anti_overfit_analyst"],
            "anti_overfit_analyst": ["backtest_diagnostics"],
        }
        layers = topological_layers(adj)
        assert layers[0] == ["researcher"]
        assert sorted(layers[1]) == ["data_quality", "factor_analyst"]
        assert layers[2] == ["strategist"]
        assert layers[3] == ["portfolio_construction"]
        assert layers[4] == ["risk_controller"]
        assert layers[5] == ["attribution_analyst"]
        assert layers[6] == ["anti_overfit_analyst"]
        assert layers[7] == ["backtest_diagnostics"]

    def test_round_execution_time(self):
        reg = AgentRegistry()
        reg.register(StubExecutor("a"))
        adj = {"a": []}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "test")
        assert result.total_duration_ms >= 0
