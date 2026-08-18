import pytest

from strategy_research.core.swarm.types import AgentCall, AgentStatus


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