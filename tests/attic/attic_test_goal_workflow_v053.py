"""Archived from tests/test_goal_workflow_v053.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


    @pytest.mark.skip(reason="_build_controller removed (P8 cleanup)")
    def test_build_controller_has_agents(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(
            config=workflow_config,
            session_id="test",
        )
        controller = runner._build_controller()
        if controller is not None:
            # If controller is built, it should have a non-empty registry
            registry = getattr(controller, "_registry", None)
            assert registry is not None
            # Should have at least one registered agent from config
            assert len(registry) > 0
            # Registered agents should match config agent IDs
            config_agent_ids = {a.id for a in workflow_config.agents}
            registered_ids = set(registry.list_agents())
            assert config_agent_ids == registered_ids


    @pytest.mark.skip(reason="_build_controller removed (P8 cleanup)")
    def test_build_controller_returns_controller(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(
            config=workflow_config,
            session_id="test",
        )
        controller = runner._build_controller()
        # Should return either a WorkflowController or None (graceful fallback)
        if controller is not None:
            assert hasattr(controller, "execute_agent")


    @pytest.mark.skip(reason="PromptBuilder no longer used by _execute_agent (P4 unified)")
    def test_prompt_builder_used(self):
        """Verify that the module can be imported and the fix is in place."""
        import inspect

        from strategy_research.core.swarm.runtime import SwarmRuntime
        source = inspect.getsource(SwarmRuntime._execute_agent)
        # After P1.5 fix, should reference PromptBuilder
        assert "PromptBuilder" in source or "_build_prompt" in source
