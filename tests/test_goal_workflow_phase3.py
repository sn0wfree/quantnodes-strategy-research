"""Tests for Phase 3 — SwarmRuntime hooks, GoalWorkflowHook, expression DSL.

Coverage:
  SwarmHook (P3.1):
    * SwarmHook Protocol is defined
    * SwarmRuntime.execute accepts hooks parameter
    * Hook callbacks are called at correct lifecycle points

  GoalWorkflowHook (P3.2):
    * on_agent_complete collects evidence
    * on_layer_complete checks criteria coverage
    * should_stop returns True when completed
    * extract_output handles AgentResult and dict

  SwarmPreset unification (P3.3):
    * GoalWorkflowConfig.to_swarm_preset converts correctly
    * SwarmPreset accepts goal/completion/branches fields

  Expression DSL (P3.7):
    * evaluate numeric comparison
    * evaluate string comparison
    * evaluate dot-path resolution
    * evaluate returns False for None path
    * evaluate raises on bad expression

  Immediate cancel (P3.4):
    * GoalWorkflowState.cancelled field exists
    * pause(immediate=True) sets cancelled flag

  MetricsObserver (P3.10):
    * Tracks event counts
    * summary returns dict
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from strategy_research.core.goal.event_bus import (
    CollectingObserver,
    MetricsObserver,
    WorkflowEventBus,
)
from strategy_research.core.goal.expression_evaluator import (
    evaluate_condition,
)
from strategy_research.core.goal.workflow import (
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
    GoalWorkflowRunner,
    GoalWorkflowState,
)
from strategy_research.core.goal.workflow_hook import GoalWorkflowHook
from strategy_research.core.swarm.runtime import AgentResult, SwarmPreset, SwarmRuntime
from strategy_research.core.workflow.types import AgentStatus, SwarmHook

# ── SwarmPreset unification (P3.3) ───────────────────────────


class TestSwarmPresetUnification:
    def test_goal_fields_default_none(self):
        preset = SwarmPreset(name="test")
        assert preset.goal is None
        assert preset.completion is None
        assert preset.branches == []

    def test_goal_fields_accepted(self):
        preset = SwarmPreset(
            name="test",
            goal={"default_criteria": ["c1"]},
            completion={"mode": "auto"},
            branches=[{"condition": "x", "action": "skip", "target": "y"}],
        )
        assert preset.goal["default_criteria"] == ["c1"]
        assert preset.completion["mode"] == "auto"
        assert len(preset.branches) == 1

    def test_to_swarm_preset_conversion(self):
        config = GoalWorkflowConfig(
            name="test_wf",
            description="test",
            agents=[
                GoalAgentConfig(id="a", prompt_file=".prompts/a.md", tools=["t1"]),
                GoalAgentConfig(id="b", prompt_file=".prompts/b.md", input_from=["a"]),
            ],
            dag={"a": [], "b": ["a"]},
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
        )
        preset = config.to_swarm_preset()
        assert preset.name == "test_wf"
        assert len(preset.agents) == 2
        assert preset.dag == {"a": [], "b": ["a"]}
        assert preset.goal["default_criteria"] == ["c1"]
        assert preset.completion["mode"] == "auto"


# ── Expression DSL (P3.7) ────────────────────────────────────


class TestExpressionEvaluator:
    def test_numeric_lt(self):
        data = {"a": {"output": {"sharpe": 0.5}}}
        assert evaluate_condition("a.output.sharpe < 0.3", data) is False
        assert evaluate_condition("a.output.sharpe > 0.3", data) is True

    def test_numeric_eq(self):
        data = {"a": {"output": {"val": 42}}}
        assert evaluate_condition("a.output.val == 42", data) is True
        assert evaluate_condition("a.output.val != 42", data) is False

    def test_numeric_ge_le(self):
        data = {"a": {"output": {"x": 10}}}
        assert evaluate_condition("a.output.x >= 10", data) is True
        assert evaluate_condition("a.output.x <= 10", data) is True
        assert evaluate_condition("a.output.x >= 11", data) is False

    def test_string_eq(self):
        data = {"a": {"output": {"verdict": "fail"}}}
        assert evaluate_condition('a.output.verdict == "fail"', data) is True
        assert evaluate_condition("a.output.verdict == 'pass'", data) is False

    def test_none_path_returns_false(self):
        data = {"a": {"output": {}}}
        assert evaluate_condition("a.output.missing < 1", data) is False

    def test_empty_data(self):
        assert evaluate_condition("a.b.c > 0", {}) is False

    def test_bad_expression_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            evaluate_condition("not a valid expression!!!", {})

    def test_boolean_literals(self):
        assert evaluate_condition("true", {}) is True
        assert evaluate_condition("false", {}) is False


# ── GoalWorkflowHook (P3.2) ──────────────────────────────────


class TestGoalWorkflowHook:
    def _make_hook(self):
        store = mock.MagicMock()
        store.get_current_snapshot.return_value = {
            "goal": {"goal_id": "g1", "status": "active"},
            "criteria": [
                {"criterion_id": "c1", "status": "pending", "required": True},
            ],
            "evidence": [],
            "evidence_count": 0,
        }
        bus = CollectingObserver()
        hook = GoalWorkflowHook(
            session_id="s1",
            goal_id="g1",
            evidence_map={"researcher": 0},
            store=store,
            workflow_name="test_wf",
            event_bus=WorkflowEventBus(),
        )
        hook._event_bus.subscribe(bus)
        return hook, store, bus

    def test_should_stop_false_by_default(self):
        hook, _, _ = self._make_hook()
        assert hook.should_stop() is False

    def test_extract_output_from_agent_result(self):
        hook, _, _ = self._make_hook()
        result = AgentResult(agent_id="a", output="hello")
        assert hook._extract_output(result) == "hello"

    def test_extract_output_from_dict(self):
        hook, _, _ = self._make_hook()
        assert hook._extract_output({"answer": "world"}) == "world"

    def test_extract_output_from_none(self):
        hook, _, _ = self._make_hook()
        assert hook._extract_output(None) == ""

    def test_on_agent_complete_collects_evidence(self):
        hook, store, bus = self._make_hook()
        result = AgentResult(
            agent_id="researcher",
            status=AgentStatus.SUCCESS,
            output="This is a meaningful research output with enough content",
        )
        hook.on_agent_complete("researcher", result, {})
        assert hook.evidence_count == 1

    def test_on_agent_complete_skips_unknown_agent(self):
        hook, store, bus = self._make_hook()
        result = AgentResult(agent_id="unknown", output="hello")
        hook.on_agent_complete("unknown", result, {})
        assert hook.evidence_count == 0

    def test_should_stop_after_completion(self):
        hook, store, bus = self._make_hook()
        hook._completed = True
        assert hook.should_stop() is True


# ── SwarmRuntime hooks (P3.1) ─────────────────────────────────


class TestSwarmRuntimeHooks:
    def test_execute_accepts_hooks(self):
        """SwarmRuntime.execute signature includes hooks param."""
        import inspect
        sig = inspect.signature(SwarmRuntime.execute)
        assert "hooks" in sig.parameters

    def test_hooks_called_during_execute(self):
        """Hooks receive on_layer_start, on_agent_complete, on_layer_complete."""
        hook = mock.MagicMock(spec=SwarmHook)
        hook.name = "test_hook"
        hook.should_stop.return_value = False

        runtime = SwarmRuntime()
        preset = SwarmPreset(
            name="test",
            agents=[],
            dag={"a": []},
        )
        # No agents → hook should still get layer events
        runtime.execute(preset, Path('/tmp'), 'task', hooks=[hook])
        # No agents registered, so no agent_complete
        hook.on_layer_start.assert_called_once()
        hook.on_layer_complete.assert_called_once()

    def test_should_stop_terminates_early(self):
        """should_stop returning True stops DAG after current layer."""
        hook = mock.MagicMock(spec=SwarmHook)
        hook.name = "stop_hook"
        hook.should_stop.return_value = True

        runtime = SwarmRuntime()
        preset = SwarmPreset(
            name="test",
            agents=[],
            dag={"a": [], "b": ["a"]},
        )
        runtime.execute(preset, Path('/tmp'), 'task', hooks=[hook])
        # Only first layer should have executed
        assert hook.on_layer_start.call_count == 1


# ── GoalWorkflowState P3.4 ───────────────────────────────────


class TestGoalWorkflowStateP3:
    def test_cancelled_field_exists(self):
        state = GoalWorkflowState()
        assert state.cancelled is False

    def test_cancelled_set_to_true(self):
        state = GoalWorkflowState()
        state.cancelled = True
        assert state.cancelled is True


# ── MetricsObserver (P3.10) ───────────────────────────────────


class TestMetricsObserver:
    def test_tracks_event_counts(self):
        obs = MetricsObserver()
        obs.on_event("agent_start", {"agent_id": "a"})
        obs.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 1.5})
        obs.on_event("agent_start", {"agent_id": "b"})
        assert obs.event_counts["agent_start"] == 2
        assert obs.event_counts["agent_complete"] == 1

    def test_summary_returns_dict(self):
        obs = MetricsObserver()
        obs.on_event("agent_complete", {"agent_id": "a", "elapsed_s": 2.0})
        summary = obs.summary()
        assert "event_counts" in summary
        assert "agent_avg_timings" in summary
        assert summary["agent_avg_timings"]["a"] == 2.0

    def test_clear(self):
        obs = MetricsObserver()
        obs.on_event("e", {})
        obs.clear()
        assert obs.event_counts == {}
        assert obs.agent_timings == {}


# ── Branch DSL wiring (P0-1) ─────────────────────────────────


class TestBranchWiring:
    """SwarmRuntime evaluates branches and applies skip/retry (P3.7)."""

    def _make_controller(self, outputs: dict[str, str]):
        """Build a controller whose agents return fixed JSON outputs."""
        class _StubExecutor:
            def __init__(self, agent_id, out):
                self.agent_id = agent_id
                self.out = out

            def execute(self, agent_call, task, workspace):
                return self.out

        from strategy_research.core.workflow.controller import WorkflowController

        class _StubController(WorkflowController):
            def execute_agent(self, agent_call, task, workspace=None):
                return outputs.get(agent_call.agent_name, "{}")

        return _StubController(registry=mock.MagicMock(), adj={}, config=mock.MagicMock())

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

    def test_layer_results_parses_nested_answer(self):
        """_build_layer_results merges inner answer dict for output.field."""
        runtime = SwarmRuntime()
        ar = AgentResult(
            agent_id="risk",
            status=AgentStatus.SUCCESS,
            output='{"agent": "risk", "answer": "{\\"max_drawdown\\": -0.3, '
                   '\\"verdict\\": \\"fail\\"}"}',
        )
        lr = runtime._build_layer_results({"risk": ar})
        assert lr["risk"]["output"]["max_drawdown"] == -0.3
        assert lr["risk"]["output"]["verdict"] == "fail"

    def test_layer_results_non_json_output(self):
        runtime = SwarmRuntime()
        ar = AgentResult(agent_id="a", status=AgentStatus.SUCCESS, output="plain text")
        lr = runtime._build_layer_results({"a": ar})
        assert "output" in lr["a"]


# ── Progress tracking (P1-2) ─────────────────────────────────


class TestProgressTracking:
    def test_hook_updates_runner_state(self):
        """Hook on_layer_start / on_agent_complete update runner state."""
        from strategy_research.core.goal.workflow import GoalWorkflowState
        state = GoalWorkflowState()
        runner = mock.MagicMock()
        runner._state = state

        store = mock.MagicMock()
        hook = GoalWorkflowHook(
            session_id="s1", goal_id="g1",
            evidence_map={"a": 0}, store=store, runner=runner,
        )
        hook.on_layer_start(0, ["a", "b"], {})
        assert state.current_layer == 1
        assert state.agent_statuses["a"] == "running"

        result = AgentResult(agent_id="a", status=AgentStatus.SUCCESS,
                             output="some meaningful output text here")
        hook.on_agent_complete("a", result, {})
        assert state.agent_statuses["a"] == "success"

    def test_progress_total_layers_uses_topological(self):
        from strategy_research.core.goal.workflow import (
            GoalAgentConfig,
            GoalWorkflowConfig,
            GoalWorkflowGoalConfig,
        )
        config = GoalWorkflowConfig(
            name="wf",
            description="",
            agents=[
                GoalAgentConfig(id="a", prompt_file=".prompts/a.md"),
                GoalAgentConfig(id="b", prompt_file=".prompts/b.md"),
                GoalAgentConfig(id="c", prompt_file=".prompts/c.md"),
            ],
            dag={"a": [], "b": ["a"], "c": ["a", "b"]},
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
        )
        runner = GoalWorkflowRunner(config, "s1")
        progress = runner.get_progress()
        # 3 agents, 3 layers (a | b | c)
        assert progress["total_layers"] == 3


# ── True resume from checkpoint (P1.8) ────────────────────────


class TestResumeAndContinue:
    """GoalWorkflowRunner.resume_and_continue: real resume from
    a saved checkpoint, reusing the existing goal_id."""

    def _setup_resume(self, tmp_path, monkeypatch):
        """Build a runner with a saved checkpoint + stub controller.

        Returns (runner, executed_agent_ids) where executed_agent_ids
        records which agents SwarmRuntime actually executed (i.e.
        excludes the pre-completed ones).
        """
        from strategy_research.core.goal.workflow import (
            GoalAgentConfig,
            GoalWorkflowConfig,
            GoalWorkflowGoalConfig,
        )
        from strategy_research.core.workflow.controller import WorkflowController

        executed: list[str] = []

        class _StubController(WorkflowController):
            def execute_agent(self, agent_call, task, workspace=None):
                executed.append(agent_call.agent_name)
                return '{"answer": "stub"}'

        config = GoalWorkflowConfig(
            name="wf",
            description="",
            agents=[
                GoalAgentConfig(id="a", prompt_file=".prompts/a.md"),
                GoalAgentConfig(id="b", prompt_file=".prompts/b.md"),
                GoalAgentConfig(id="c", prompt_file=".prompts/c.md"),
            ],
            dag={"a": [], "b": ["a"], "c": ["a", "b"]},
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
        )

        # Point checkpoint store at tmp_path so the test is hermetic
        monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR", str(tmp_path))

        runner = GoalWorkflowRunner(
            config=config, session_id="s_resume",
        )
        runner._goal_id = "g_resume"

        # Save a checkpoint: layer 0 ('a') completed, layer 1 about
        # to execute next.
        runner._state.current_layer = 1  # 1-based, hook convention
        runner._state.evidence_count = 0
        runner._state.agent_statuses = {"a": "success"}
        from strategy_research.core.goal.workflow_hook import GoalWorkflowHook
        hook = GoalWorkflowHook.__new__(GoalWorkflowHook)
        hook._layer_results = {
            "a": {"output": '{"answer": "pre-saved"}'},
        }
        hook._completed = False
        hook._evidence_count = 0
        runner._hook = hook
        runner.checkpoint()

        # Stub the SwarmRuntime.build_controller via runner._build_controller
        runner._build_controller = lambda: _StubController(
            registry=mock.MagicMock(), adj={}, config=mock.MagicMock(),
        )

        return runner, executed

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

    def test_resume_raises_when_no_checkpoint(self, tmp_path, monkeypatch):
        """Calling resume_and_continue without a checkpoint must raise."""
        from strategy_research.core.goal.workflow import (
            GoalAgentConfig,
            GoalWorkflowConfig,
            GoalWorkflowGoalConfig,
        )
        monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR", str(tmp_path))

        config = GoalWorkflowConfig(
            name="wf",
            description="",
            agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md")],
            dag={"a": []},
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
        )
        runner = GoalWorkflowRunner(config=config, session_id="s_nope")
        runner._goal_id = "g_nope"

        with pytest.raises(FileNotFoundError):
            asyncio.run(runner.resume_and_continue())


# ── pre_completed seeding in SwarmRuntime (P1.8) ──────────────


class TestSwarmRuntimePreCompleted:
    """SwarmRuntime.execute(pre_completed=..., start_layer=...)
    skips pre-completed agents and starts execution from start_layer."""

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
