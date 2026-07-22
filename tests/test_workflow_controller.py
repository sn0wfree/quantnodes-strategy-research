import pytest
from strategy_research.core.workflow.controller import (
    AgentExecution,
    ControllerConfig,
    RoundExecution,
    WorkflowController,
)
from strategy_research.core.workflow.agents import AgentRegistry
from strategy_research.core.workflow.types import AgentStatus


class DummyAgent:
    def __init__(self, name: str, result: dict | None = None, raise_error: bool = False):
        self._name = name
        self._result = result or {"status": "ok"}
        self._raise_error = raise_error

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        if self._raise_error:
            raise RuntimeError(f"{self._name} failed")
        return self._result


class TestControllerConfig:
    def test_defaults(self):
        config = ControllerConfig()
        assert config.max_retries == 3
        assert config.timeout_seconds == 60.0
        assert config.retry_delay == 1.0

    def test_custom(self):
        config = ControllerConfig(max_retries=5, timeout_seconds=30.0)
        assert config.max_retries == 5
        assert config.timeout_seconds == 30.0


class TestAgentExecution:
    def test_defaults(self):
        from strategy_research.core.workflow.types import AgentCall
        call = AgentCall(agent_name="test", prompt="p")
        exec_ = AgentExecution(call=call)
        assert exec_.status == AgentStatus.PENDING
        assert exec_.output == {}
        assert exec_.error == ""
        assert exec_.retries == 0


class TestRoundExecution:
    def test_defaults(self):
        round_exec = RoundExecution(round_num=1)
        assert round_exec.round_num == 1
        assert round_exec.executions == []
        assert round_exec.keep is False


class TestWorkflowController:
    def test_build_agent_chain(self):
        reg = AgentRegistry()
        adj = {"a": ["b"], "b": ["c"]}
        ctrl = WorkflowController(reg, adj)
        chain = ctrl.build_agent_chain()
        assert chain == ["a", "b", "c"]

    def test_layers(self):
        reg = AgentRegistry()
        adj = {"a": ["c"], "b": ["c"]}
        ctrl = WorkflowController(reg, adj)
        assert ctrl.layers == [["a", "b"], ["c"]]

    def test_execute_round_skip_unregistered(self):
        reg = AgentRegistry()
        adj = {"a": ["b"]}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "test prompt")
        assert len(result.executions) == 2
        assert all(e.status == AgentStatus.SKIPPED for e in result.executions)

    def test_execute_round_success(self):
        reg = AgentRegistry()
        reg.register(DummyAgent("a"))
        reg.register(DummyAgent("b"))
        adj = {"a": ["b"]}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "test prompt")
        assert len(result.executions) == 2
        assert result.executions[0].status == AgentStatus.SUCCESS
        assert result.executions[1].status == AgentStatus.SUCCESS

    def test_execute_round_with_retry(self):
        reg = AgentRegistry()
        reg.register(DummyAgent("a"))
        reg.register(DummyAgent("b", raise_error=True))
        adj = {"a": ["b"]}
        config = ControllerConfig(max_retries=2, retry_delay=0.01)
        ctrl = WorkflowController(reg, adj, config)
        result = ctrl.execute_round(1, "test prompt")
        assert result.executions[1].status == AgentStatus.ERROR
        assert result.executions[1].retries == 2

    def test_execute_round_passes_context(self):
        reg = AgentRegistry()
        reg.register(DummyAgent("a"))
        adj = {"a": []}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "prompt", context={"key": "value"})
        assert result.executions[0].call.context["key"] == "value"

    def test_input_from_upstream(self):
        reg = AgentRegistry()
        reg.register(DummyAgent("a"))
        reg.register(DummyAgent("b"))
        adj = {"a": ["b"]}
        ctrl = WorkflowController(reg, adj)
        result = ctrl.execute_round(1, "prompt")
        b_ctx = result.executions[1].call.context
        assert "input_from" in b_ctx
        assert "a" in b_ctx["input_from"]
