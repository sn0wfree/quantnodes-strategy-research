import pytest
from strategy_research.core.workflow.types import (
    AgentCall,
    AgentStatus,
    RoundResult,
    SwarmTask,
)


class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.PENDING == "pending"
        assert AgentStatus.RUNNING == "running"
        assert AgentStatus.SUCCESS == "success"
        assert AgentStatus.ERROR == "error"
        assert AgentStatus.SKIPPED == "skipped"

    def test_string_comparison(self):
        assert AgentStatus.SUCCESS == "success"
        assert AgentStatus("pending") == AgentStatus.PENDING

    def test_is_frozen(self):
        with pytest.raises(AttributeError):
            AgentStatus.PENDING = "changed"


class TestAgentCall:
    def test_basic_creation(self):
        call = AgentCall(agent_name="researcher", prompt="test prompt")
        assert call.agent_name == "researcher"
        assert call.prompt == "test prompt"
        assert call.context == {}
        assert call.metadata == {}

    def test_with_context(self):
        call = AgentCall(
            agent_name="strategist",
            prompt="generate",
            context={"upstream": {"data": "value"}},
        )
        assert call.context == {"upstream": {"data": "value"}}

    def test_frozen(self):
        call = AgentCall(agent_name="a", prompt="p")
        with pytest.raises(AttributeError):
            call.agent_name = "b"


class TestRoundResult:
    def test_basic_creation(self):
        result = RoundResult(round_num=1)
        assert result.round_num == 1
        assert result.agent_results == []
        assert result.keep is False
        assert result.calmar == 0.0
        assert result.sharpe == 0.0

    def test_with_results(self):
        result = RoundResult(
            round_num=2,
            agent_results=[AgentStatus.SUCCESS, AgentStatus.ERROR],
            keep=True,
            calmar=0.5,
            sharpe=0.8,
        )
        assert len(result.agent_results) == 2
        assert result.keep is True


class TestSwarmTask:
    def test_basic_creation(self):
        task = SwarmTask(strategy_id="s1", workspace="/tmp/ws")
        assert task.strategy_id == "s1"
        assert task.workspace == "/tmp/ws"
        assert task.rounds == []

    def test_with_rounds(self):
        r1 = RoundResult(round_num=1, keep=True, calmar=0.6)
        task = SwarmTask(strategy_id="s1", workspace="/tmp/ws", rounds=[r1])
        assert len(task.rounds) == 1
        assert task.rounds[0].calmar == 0.6

    def test_frozen(self):
        task = SwarmTask(strategy_id="s1", workspace="/tmp/ws")
        with pytest.raises(AttributeError):
            task.strategy_id = "s2"
