"""Archived from tests/test_goal_workflow_phase3.py — dead tests, kept for reference.

Every test below was @pytest.mark.skip'd because the code under test
was removed in the P4/P8/Phase-A cleanups (see each skip reason).
Not collected: tests/conftest.py sets collect_ignore_glob=["attic/*"].
"""

import pytest  # noqa: F401 — retained from the archived sources


    @pytest.mark.skip(reason="Mock controller no longer drives SwarmRuntime._execute_agent (P4 unified)")
    def test_skip_removes_target_from_later_layer(self):
        outputs = {
            "risk": '{"agent": "risk", "status": "success", '
                    '"answer": "{\\"max_drawdown\\": -0.3, \\"verdict\\": \\"fail\\"}"}',
            "portfolio": '{"agent": "portfolio", "status": "success", "answer": "{}"}',
        }
        runtime = SwarmRuntime(controller=self._make_controller(outputs))
        preset = SwarmPreset(
            name="wf",
            agents=[],
            dag={"risk": [], "portfolio": ["risk"]},
            branches=[{
                "condition": "risk.output.max_drawdown < -0.2",
                "action": "skip",
                "target": "portfolio",
                "reason": "回撤过大",
            }],
        )
        # Register agents via AgentCall
        from strategy_research.core.workflow.types import AgentCall
        preset.agents = [
            AgentCall(agent_name="risk", prompt=".prompts/risk.md",
                      context={"tools": [], "input_from": [], "evidence_criterion": 0,
                               "timeout": 30, "max_retries": 0}),
            AgentCall(agent_name="portfolio", prompt=".prompts/pf.md",
                      context={"tools": [], "input_from": ["risk"], "evidence_criterion": 0,
                               "timeout": 30, "max_retries": 0}),
        ]
        result = runtime.execute(preset, Path("/tmp"), "task")
        # portfolio should be skipped → never executed
        assert "portfolio" not in result.agent_results
        assert "risk" in result.agent_results


    @pytest.mark.skip(reason="WorkflowController module removed (P8 cleanup)")
    def test_retry_reruns_target_in_next_layer(self):
        outputs = {
            "a": '{"agent": "a", "status": "success", "answer": "{\\"x\\": 1}"}',
            "b": '{"agent": "b", "status": "success", "answer": "{}"}',
        }
        runtime = SwarmRuntime(controller=self._make_controller(outputs))
        from strategy_research.core.workflow.types import AgentCall
        preset = SwarmPreset(
            name="wf",
            agents=[
                AgentCall(agent_name="a", prompt=".prompts/a.md",
                          context={"tools": [], "evidence_criterion": 0}),
                AgentCall(agent_name="b", prompt=".prompts/b.md",
                          context={"tools": [], "evidence_criterion": 0}),
            ],
            dag={"a": [], "b": ["a"]},
            branches=[{
                "condition": "a.output.x == 1",
                "action": "retry",
                "target": "b",
            }],
        )
        result = runtime.execute(preset, Path("/tmp"), "task")
        # retry → b is re-added to next layer, executed again
        assert "a" in result.agent_results
        assert "b" in result.agent_results


    @pytest.mark.skip(reason="Mock controller no longer drives SwarmRuntime._execute_agent (P4 unified)")
    def test_resume_loads_layer_results_and_skips_completed_layer(
        self, tmp_path, monkeypatch
    ):
        from strategy_research.core.goal.store import GoalStore

        runner, executed = self._setup_resume(tmp_path, monkeypatch)

        # Need a real GoalStore so resume_and_continue can reload the
        # goal's objective for prompts.
        store = GoalStore()
        # Seed a goal row so the store can find it.
        store.replace_goal(
            session_id="s_resume",
            objective="resume me",
            criteria=["c1"],
            workflow_id="wf",
        )
        goal = store.get_current_goal("s_resume")
        # Align goal_id with the runner's expectation
        runner._goal_id = goal.goal_id
        # Re-save checkpoint under the real goal_id
        runner.checkpoint()

        asyncio.run(runner.resume_and_continue())

        # Layer 0 ('a') was pre-completed → must NOT be re-executed.
        # Layers 1 + 2 ('b', 'c') execute normally.
        assert "a" not in executed, f"pre-completed agent re-executed: {executed}"
        assert "b" in executed
        assert "c" in executed
        # runner._goal_id reused (no new replace_goal)
        assert runner._goal_id == goal.goal_id


    @pytest.mark.skip(reason="Mock controller no longer drives SwarmRuntime._execute_agent (P4 unified)")
    def test_pre_completed_skips_agents_and_starts_from_start_layer(self):
        from strategy_research.core.swarm.runtime import (
            AgentResult,
            SwarmPreset,
            SwarmRuntime,
        )
        from strategy_research.core.workflow.types import AgentCall

        executed: list[str] = []

        class _StubController:
            def execute_agent(self, agent_call, task, workspace=None):
                executed.append(agent_call.agent_name)
                return '{"answer": "stub"}'

        # Pretend agents 'a' (layer 0) and 'b' (layer 1) already ran
        pre = {
            "a": AgentResult(agent_id="a", status=AgentStatus.SUCCESS,
                             output='{"answer": "pre-a"}'),
            "b": AgentResult(agent_id="b", status=AgentStatus.SUCCESS,
                             output='{"answer": "pre-b"}'),
        }
        preset = SwarmPreset(
            name="wf",
            agents=[
                AgentCall(agent_name="a", prompt=".prompts/a.md", context={}),
                AgentCall(agent_name="b", prompt=".prompts/b.md", context={}),
                AgentCall(agent_name="c", prompt=".prompts/c.md", context={}),
            ],
            dag={"a": [], "b": ["a"], "c": ["a", "b"]},
        )

        runtime = SwarmRuntime(controller=_StubController())
        result = runtime.execute(
            preset, Path("/tmp"), "task", hooks=[],
            pre_completed=pre, start_layer=2,
        )

        # Only 'c' (layer 2) executes; pre-completed agents are skipped
        # but their results remain in result.agent_results.
        assert executed == ["c"]
        assert "a" in result.agent_results
        assert "b" in result.agent_results
        assert "c" in result.agent_results


    @pytest.mark.skip(reason="Mock controller no longer drives SwarmRuntime._execute_agent (P4 unified)")
    def test_pre_completed_partial_layer_skips_only_completed(self):
        """Within the current layer, only pre-completed agents are skipped."""
        from strategy_research.core.swarm.runtime import (
            AgentResult,
            SwarmPreset,
            SwarmRuntime,
        )
        from strategy_research.core.workflow.types import AgentCall

        executed: list[str] = []

        class _StubController:
            def execute_agent(self, agent_call, task, workspace=None):
                executed.append(agent_call.agent_name)
                return '{"answer": "stub"}'

        # Layer 0 has 'a', 'b'; 'a' is pre-completed.
        pre = {"a": AgentResult(agent_id="a", status=AgentStatus.SUCCESS,
                                 output='{"answer": "pre-a"}')}
        preset = SwarmPreset(
            name="wf",
            agents=[
                AgentCall(agent_name="a", prompt=".prompts/a.md", context={}),
                AgentCall(agent_name="b", prompt=".prompts/b.md", context={}),
            ],
            dag={"a": [], "b": ["a"]},
        )
        runtime = SwarmRuntime(controller=_StubController())
        result = runtime.execute(
            preset, Path("/tmp"), "task", hooks=[],
            pre_completed=pre, start_layer=0,
        )
        assert executed == ["b"]
        assert "a" in result.agent_results
        assert "b" in result.agent_results
