"""Archived from tests/test_swarm.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


    @pytest.mark.skip(reason="WorkflowController no longer used by _execute_agent (P4)")
    def test_execute_with_failure(self, tmp_path):
        preset = SwarmPreset(
            name="failing",
            agents=[
                AgentCall(agent_name="a1", prompt=""),
            ],
            dag={"a1": []},
        )

        mock_controller = MagicMock()
        mock_controller.execute_agent.side_effect = RuntimeError("agent failed")

        runtime = SwarmRuntime(controller=mock_controller)
        result = runtime.execute(preset, tmp_path, "test task")

        assert not result.success
        assert result.agent_results["a1"].status == AgentStatus.ERROR
