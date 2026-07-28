"""Tests for Phase 2 refactor — design patterns + reuse.

Coverage:
  AgentRunner (R3):
    * StubAgentRunner returns expected stub
    * AgentRunnerFactory.create dispatches by type
    * AgentRunnerFactory.list_types enumerates runners
    * AgentRunnerRegistry supports dynamic registration

  CompletionStrategy (R6):
    * AutoCompleteStrategy dispatches to update_status
    * LiteCompleteStrategy dispatches to complete_lite
    * ManualCompleteStrategy is no-op
    * CompletionStrategyFactory.get dispatches by mode

  ValidatorRegistry (R9):
    * register_default_validators registers 9 agents
    * get returns the validator for known agent
    * get returns None for unknown agent
    * is_valid_result handles None and valid objects

  Decorators (R7):
    * with_retry retries on exception
    * with_retry raises after max_retries
    * with_timeout raises TimeoutError
    * with_validation retries on invalid output
    * with_evidence_collection calls collector

  EventBus (R8):
    * emit dispatches to all observers
    * observer failures don't break the bus
    * subscribe / unsubscribe work
    * CollectingObserver records events
    * GoalPanelObserver forwards to panel
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest

from strategy_research.core.goal.completion_strategy import (
    AutoCompleteStrategy,
    CompletionStrategyFactory,
    LiteCompleteStrategy,
    ManualCompleteStrategy,
)
from strategy_research.core.goal.event_bus import (
    CollectingObserver,
    GoalPanelObserver,
    LoggerObserver,
    WorkflowEventBus,
)
from strategy_research.core.goal.validator_registry import (
    ValidatorRegistry,
    register_default_validators,
)
from strategy_research.core.workflow.agent_runner import (
    AgentRunner,
    AgentRunnerFactory,
    AgentRunnerRegistry,
    StubAgentRunner,
)
from strategy_research.core.workflow.decorators import (
    with_evidence_collection,
    with_retry,
    with_timeout,
    with_validation,
)


# ── AgentRunner (R3) ──────────────────────────────────────────


class TestStubAgentRunner:
    @pytest.mark.asyncio
    async def test_returns_stub_answer(self):
        runner = StubAgentRunner()
        result = await runner.run("test_agent", "prompt", ["tool1"], {})
        assert result["answer"] == "[stub] test_agent completed"


class TestAgentRunnerFactory:
    def test_create_stub(self):
        runner = AgentRunnerFactory.create("stub")
        assert isinstance(runner, StubAgentRunner)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown runner type"):
            AgentRunnerFactory.create("unknown_type")

    def test_list_types(self):
        types = AgentRunnerFactory.list_types()
        assert "stub" in types
        assert "swarm_worker" in types
        assert "agent_loop" in types

    def test_swarm_worker_requires_no_args(self):
        runner = AgentRunnerFactory.create("swarm_worker")
        assert runner is not None


class TestAgentRunnerRegistry:
    def test_register_and_create(self):
        class MyRunner:
            async def run(self, agent_id, prompt, tools, context):
                return {"answer": "custom"}

        AgentRunnerRegistry.register("custom", MyRunner)
        runner = AgentRunnerRegistry.create("custom")
        result = asyncio.run(runner.run("a", "p", [], {}))
        assert result["answer"] == "custom"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown runner"):
            AgentRunnerRegistry.create("does_not_exist")


# ── CompletionStrategy (R6) ───────────────────────────────────


class TestCompletionStrategies:
    @pytest.mark.asyncio
    async def test_auto_strategy_calls_update_status(self):
        store = mock.MagicMock()
        strategy = AutoCompleteStrategy()
        await strategy.complete(
            store, "sid", "gid",
            [{"criterion_id": "c1", "required": True}],
            [{"evidence_id": "e1", "criterion_id": "c1"}],
            "test_workflow",
        )
        store.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_lite_strategy_calls_complete_lite(self):
        store = mock.MagicMock()
        strategy = LiteCompleteStrategy()
        await strategy.complete(
            store, "sid", "gid", [], [], "test_workflow",
        )
        store.complete_lite.assert_called_once()
        store.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_strategy_is_noop(self):
        store = mock.MagicMock()
        strategy = ManualCompleteStrategy()
        result = await strategy.complete(
            store, "sid", "gid", [], [], "test_workflow",
        )
        assert result is True
        store.update_status.assert_not_called()
        store.complete_lite.assert_not_called()


class TestCompletionStrategyFactory:
    def test_get_auto(self):
        s = CompletionStrategyFactory.get("auto")
        assert isinstance(s, AutoCompleteStrategy)

    def test_get_lite(self):
        s = CompletionStrategyFactory.get("lite")
        assert isinstance(s, LiteCompleteStrategy)

    def test_get_manual(self):
        s = CompletionStrategyFactory.get("manual")
        assert isinstance(s, ManualCompleteStrategy)

    def test_unknown_falls_back_to_auto(self):
        s = CompletionStrategyFactory.get("nonsense")
        assert isinstance(s, AutoCompleteStrategy)

    def test_list_modes(self):
        modes = CompletionStrategyFactory.list_modes()
        assert "auto" in modes
        assert "lite" in modes
        assert "manual" in modes


# ── ValidatorRegistry (R9) ────────────────────────────────────


class TestValidatorRegistry:
    def setup_method(self):
        ValidatorRegistry.clear()

    def test_register_default_validators_count(self):
        register_default_validators()
        agents = ValidatorRegistry.list_registered()
        assert len(agents) == 9
        assert "researcher" in agents
        assert "factor_analyst" in agents
        assert "risk_controller" in agents

    def test_get_returns_validator(self):
        register_default_validators()
        v = ValidatorRegistry.get("researcher")
        assert v is not None

    def test_get_returns_none_for_unknown(self):
        register_default_validators()
        assert ValidatorRegistry.get("nonexistent_agent") is None

    def test_is_valid_result_for_valid(self):
        @dataclass
        class VResult:
            valid: bool = True
            errors: list[str] = None
        assert ValidatorRegistry.is_valid_result(VResult(valid=True))

    def test_is_valid_result_for_invalid(self):
        @dataclass
        class VResult:
            valid: bool = False
            errors: list[str] = None
        assert not ValidatorRegistry.is_valid_result(VResult(valid=False))

    def test_is_valid_result_for_none(self):
        assert ValidatorRegistry.is_valid_result(None) is True


# ── Decorators (R7) ──────────────────────────────────────────


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        counter = {"n": 0}

        async def succeeds():
            counter["n"] += 1
            return "ok"

        @with_retry(max_retries=3, delay_s=0)
        async def f():
            return await succeeds()

        assert await f() == "ok"
        assert counter["n"] == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        counter = {"n": 0}

        async def flaky():
            counter["n"] += 1
            if counter["n"] < 3:
                raise RuntimeError("boom")
            return "ok"

        @with_retry(max_retries=5, delay_s=0)
        async def f():
            return await flaky()

        assert await f() == "ok"
        assert counter["n"] == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        @with_retry(max_retries=2, delay_s=0)
        async def always_fails():
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError, match="nope"):
            await always_fails()


class TestWithTimeout:
    @pytest.mark.asyncio
    async def test_succeeds_within_timeout(self):
        @with_timeout(1.0)
        async def fast():
            return "ok"

        assert await fast() == "ok"

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        @with_timeout(0.05)
        async def slow():
            await asyncio.sleep(1)
            return "unreachable"

        with pytest.raises(asyncio.TimeoutError):
            await slow()


class TestWithValidation:
    @pytest.mark.asyncio
    async def test_passes_valid_output(self):
        validator = mock.MagicMock()
        validator.validate.return_value = mock.MagicMock(valid=True)

        @with_validation(validator)
        async def f():
            return {"answer": "good"}

        result = await f()
        assert result["answer"] == "good"

    @pytest.mark.asyncio
    async def test_retries_on_invalid(self):
        validator = mock.MagicMock()
        # First two invalid, third valid
        validator.validate.side_effect = [
            mock.MagicMock(valid=False, errors=["x"]),
            mock.MagicMock(valid=False, errors=["y"]),
            mock.MagicMock(valid=True),
        ]

        counter = {"n": 0}

        @with_validation(validator, max_validation_attempts=3)
        async def f():
            counter["n"] += 1
            return {"answer": f"attempt {counter['n']}"}

        result = await f()
        assert "attempt" in result["answer"]
        assert validator.validate.call_count == 3


class TestWithEvidenceCollection:
    @pytest.mark.asyncio
    async def test_calls_collector(self):
        collector = mock.MagicMock()
        collector.collect.return_value = 1

        @with_evidence_collection(collector, criterion_idx=2)
        async def f():
            return {"answer": "lots of meaningful content here"}

        result = await f()
        assert result["answer"] == "lots of meaningful content here"
        collector.collect.assert_called_once()

    @pytest.mark.asyncio
    async def test_swallows_collector_errors(self):
        collector = mock.MagicMock()
        collector.collect.side_effect = RuntimeError("DB down")

        @with_evidence_collection(collector, criterion_idx=0)
        async def f():
            return {"answer": "fine content"}

        # Should not raise — collector failure is logged and swallowed.
        result = await f()
        assert result["answer"] == "fine content"


# ── EventBus (R8) ─────────────────────────────────────────────


class TestWorkflowEventBus:
    def test_emit_to_single_observer(self):
        bus = WorkflowEventBus()
        obs = mock.MagicMock()
        bus.subscribe(obs)
        bus.emit("agent_start", agent_id="a")
        obs.on_event.assert_called_once_with("agent_start", {"agent_id": "a"})

    def test_emit_to_multiple_observers(self):
        bus = WorkflowEventBus()
        obs1, obs2 = mock.MagicMock(), mock.MagicMock()
        bus.subscribe(obs1)
        bus.subscribe(obs2)
        bus.emit("event", x=1)
        obs1.on_event.assert_called_once()
        obs2.on_event.assert_called_once()

    def test_observer_failure_does_not_break_bus(self):
        bus = WorkflowEventBus()
        good = mock.MagicMock()
        bad = mock.MagicMock()
        bad.on_event.side_effect = RuntimeError("oops")
        bus.subscribe(bad)
        bus.subscribe(good)
        bus.emit("e", v=1)  # should not raise
        good.on_event.assert_called_once()

    def test_unsubscribe(self):
        bus = WorkflowEventBus()
        obs = mock.MagicMock()
        bus.subscribe(obs)
        bus.unsubscribe(obs)
        bus.emit("e")
        obs.on_event.assert_not_called()

    def test_len(self):
        bus = WorkflowEventBus()
        assert len(bus) == 0
        bus.subscribe(mock.MagicMock())
        assert len(bus) == 1


class TestCollectingObserver:
    def test_collects_events(self):
        c = CollectingObserver()
        c.on_event("a", {"x": 1})
        c.on_event("b", {"y": 2})
        assert c.events == [("a", {"x": 1}), ("b", {"y": 2})]
        c.clear()
        assert c.events == []


class TestGoalPanelObserver:
    def test_forwards_to_panel(self):
        panel = mock.MagicMock()
        obs = GoalPanelObserver(panel)
        obs.on_event("e", {"v": 1})
        panel.on_workflow_event.assert_called_once_with("e", {"v": 1})

    def test_handles_missing_method(self):
        # Panel without on_workflow_event method shouldn't crash
        class BarePanel:
            pass
        panel = BarePanel()
        obs = GoalPanelObserver(panel)
        # Should not raise
        obs.on_event("e", {"v": 1})