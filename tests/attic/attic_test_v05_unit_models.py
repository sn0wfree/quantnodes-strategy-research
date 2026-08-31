"""Archived from tests/test_v05_unit_models.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


@pytest.mark.skip(reason="P8 cleanup: _AgentConfigExecutor removed from goal.workflow")
class TestAgentConfigExecutor:
    def test_name_returns_agent_id(self):
        assert _AgentConfigExecutor("researcher").name == "researcher"

    def test_name_with_tools(self):
        assert _AgentConfigExecutor("a", ["t1"]).name == "a"

    def test_run_returns_stub_dict(self):
        result = _AgentConfigExecutor("researcher").run("prompt")
        assert isinstance(result, dict)
        assert "answer" in result
        assert result["agent_id"] == "researcher"

    def test_run_with_context(self):
        result = _AgentConfigExecutor("a").run("p", context={"k": "v"})
        assert "[stub]" in result["answer"]
