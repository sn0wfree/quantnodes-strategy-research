"""Phase 4 - v0.5 unit tests: CompletionStrategy + PromptBuilder + AgentRegistry.

Covers:
  - AutoCompleteStrategy: builds audit rows, calls update_status
  - LiteCompleteStrategy: calls complete_lite
  - ManualCompleteStrategy: no-op
  - CompletionStrategyFactory: get/register/list_modes
  - PromptBuilder: load_prompt (cache, missing), build_prompt (template +
    base + upstream + context), clear_cache, _format_upstream, _format_context
  - AgentRegistry: register/get/list_agents/__len__/__contains__
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from strategy_research.core.goal.completion_strategy import (
    AutoCompleteStrategy,
    CompletionStrategyFactory,
    LiteCompleteStrategy,
    ManualCompleteStrategy,
)
from strategy_research.core.workflow.agents import AgentExecutor, AgentRegistry
from strategy_research.core.workflow.prompt import PromptBuilder

# ═══════════════════════════════════════════════════════════════════════
# AutoCompleteStrategy
# ═══════════════════════════════════════════════════════════════════════


class TestAutoCompleteStrategy:
    def test_complete_calls_update_status(self):
        store = MagicMock()
        strategy = AutoCompleteStrategy()
        result = asyncio.run(strategy.complete(
            store, "sess", "goal1",
            [{"criterion_id": "c0", "required": True}],
            [{"evidence_id": "e1", "criterion_id": "c0"}],
            "test_wf",
        ))
        assert result is True
        store.update_status.assert_called_once()

    def test_complete_builds_audit_rows(self):
        store = MagicMock()
        strategy = AutoCompleteStrategy()
        asyncio.run(strategy.complete(
            store, "sess", "goal1",
            [
                {"criterion_id": "c0", "required": True},
                {"criterion_id": "c1", "required": True},
            ],
            [{"evidence_id": "e1", "criterion_id": "c0"}],
            "wf",
        ))
        call_kwargs = store.update_status.call_args.kwargs
        audit = call_kwargs["audit"]
        assert len(audit) == 2
        assert audit[0].criterion_id == "c0"
        assert audit[1].criterion_id == "c1"

    def test_complete_skips_optional_criteria(self):
        store = MagicMock()
        strategy = AutoCompleteStrategy()
        asyncio.run(strategy.complete(
            store, "sess", "goal1",
            [
                {"criterion_id": "c0", "required": True},
                {"criterion_id": "c1", "required": False},
            ],
            [],
            "wf",
        ))
        audit = store.update_status.call_args.kwargs["audit"]
        assert len(audit) == 1
        assert audit[0].criterion_id == "c0"

    def test_complete_status_is_complete(self):
        store = MagicMock()
        strategy = AutoCompleteStrategy()
        asyncio.run(strategy.complete(
            store, "s", "g", [{"criterion_id": "c0", "required": True}], [], "wf",
        ))
        from strategy_research.core.goal.models import GoalStatus
        assert store.update_status.call_args.kwargs["status"] == GoalStatus.COMPLETE

    def test_complete_links_evidence_ids(self):
        store = MagicMock()
        strategy = AutoCompleteStrategy()
        asyncio.run(strategy.complete(
            store, "s", "g",
            [{"criterion_id": "c0", "required": True}],
            [
                {"evidence_id": "e1", "criterion_id": "c0"},
                {"evidence_id": "e2", "criterion_id": "c0"},
                {"evidence_id": "e3", "criterion_id": "c1"},
            ],
            "wf",
        ))
        audit = store.update_status.call_args.kwargs["audit"]
        assert audit[0].evidence_ids == ["e1", "e2"]


# ═══════════════════════════════════════════════════════════════════════
# LiteCompleteStrategy
# ═══════════════════════════════════════════════════════════════════════


class TestLiteCompleteStrategy:
    def test_complete_calls_complete_lite(self):
        store = MagicMock()
        strategy = LiteCompleteStrategy()
        result = asyncio.run(strategy.complete(
            store, "sess", "goal1", [], [], "test_wf",
        ))
        assert result is True
        store.complete_lite.assert_called_once()

    def test_complete_does_not_call_update_status(self):
        store = MagicMock()
        strategy = LiteCompleteStrategy()
        asyncio.run(strategy.complete(store, "s", "g", [], [], "wf"))
        store.update_status.assert_not_called()

    def test_complete_passes_recap(self):
        store = MagicMock()
        strategy = LiteCompleteStrategy()
        asyncio.run(strategy.complete(store, "s", "g", [], [], "my_wf"))
        call_kwargs = store.complete_lite.call_args.kwargs
        assert "my_wf" in call_kwargs["recap"]


# ═══════════════════════════════════════════════════════════════════════
# ManualCompleteStrategy
# ═══════════════════════════════════════════════════════════════════════


class TestManualCompleteStrategy:
    def test_complete_returns_true(self):
        store = MagicMock()
        strategy = ManualCompleteStrategy()
        result = asyncio.run(strategy.complete(store, "s", "g", [], [], "wf"))
        assert result is True

    def test_complete_does_not_mutate_store(self):
        store = MagicMock()
        strategy = ManualCompleteStrategy()
        asyncio.run(strategy.complete(store, "s", "g", [], [], "wf"))
        store.update_status.assert_not_called()
        store.complete_lite.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# CompletionStrategyFactory
# ═══════════════════════════════════════════════════════════════════════


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

    def test_get_unknown_falls_back_to_auto(self):
        s = CompletionStrategyFactory.get("nonexistent_mode")
        assert isinstance(s, AutoCompleteStrategy)

    def test_register_custom(self):
        custom = ManualCompleteStrategy()
        CompletionStrategyFactory.register("custom_mode", custom)
        assert CompletionStrategyFactory.get("custom_mode") is custom

    def test_list_modes_includes_builtins(self):
        modes = CompletionStrategyFactory.list_modes()
        assert "auto" in modes
        assert "lite" in modes
        assert "manual" in modes

    def test_list_modes_includes_custom(self):
        CompletionStrategyFactory.register("test_mode_xyz", ManualCompleteStrategy())
        modes = CompletionStrategyFactory.list_modes()
        assert "test_mode_xyz" in modes


# ═══════════════════════════════════════════════════════════════════════
# PromptBuilder
# ═══════════════════════════════════════════════════════════════════════


class TestPromptBuilderLoad:
    def test_load_existing_prompt(self):
        builder = PromptBuilder()
        content = builder.load_prompt("researcher")
        assert len(content) > 50

    def test_load_missing_prompt_returns_empty(self):
        builder = PromptBuilder()
        assert builder.load_prompt("nonexistent_prompt_xyz") == ""

    def test_load_uses_cache(self):
        builder = PromptBuilder()
        first = builder.load_prompt("researcher")
        second = builder.load_prompt("researcher")
        assert first is second  # same object (cached)

    def test_clear_cache(self):
        builder = PromptBuilder()
        builder.load_prompt("researcher")
        assert len(builder._cache) > 0
        builder.clear_cache()
        assert len(builder._cache) == 0

    def test_custom_prompts_dir(self, tmp_path):
        prompt_file = tmp_path / "custom_agent.md"
        prompt_file.write_text("# Custom Agent\nDo things.", encoding="utf-8")
        builder = PromptBuilder(prompts_dir=tmp_path)
        content = builder.load_prompt("custom_agent")
        assert "Custom Agent" in content


class TestPromptBuilderBuild:
    def test_build_with_template_only(self):
        builder = PromptBuilder()
        result = builder.build_prompt("researcher")
        assert len(result) > 50

    def test_build_with_base_prompt_only(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt("missing", base_prompt="Do X")
        assert "Do X" in result

    def test_build_with_template_and_base(self):
        builder = PromptBuilder()
        result = builder.build_prompt("researcher", base_prompt="Extra instructions")
        assert "Extra instructions" in result
        assert "Additional Instructions" in result

    def test_build_with_upstream_outputs(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt(
            "agent", base_prompt="task",
            upstream_outputs={"upstream_a": {"key": "value"}},
        )
        assert "Upstream Agent Outputs" in result
        assert "upstream_a" in result

    def test_build_with_context(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt(
            "agent", base_prompt="task",
            context={"tools": ["t1"], "timeout": 60},
        )
        assert "Current Context" in result
        assert "tools" in result

    def test_build_context_skips_input_from(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt(
            "agent", base_prompt="task",
            context={"input_from": ["a"], "tools": ["t1"]},
        )
        assert "Current Context" in result
        # input_from should be skipped in context formatting
        assert "input_from" not in result

    def test_build_empty_everything(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt("missing")
        assert result == ""

    def test_build_upstream_with_dict_value(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt(
            "a", base_prompt="t",
            upstream_outputs={"up": {"x": 1, "y": 2}},
        )
        assert "x" in result
        assert "1" in result

    def test_build_upstream_with_non_dict_value(self):
        builder = PromptBuilder(prompts_dir=Path("/nonexistent"))
        result = builder.build_prompt(
            "a", base_prompt="t",
            upstream_outputs={"up": "raw string output"},
        )
        assert "raw string output" in result


# ═══════════════════════════════════════════════════════════════════════
# AgentRegistry
# ═══════════════════════════════════════════════════════════════════════


class _StubExecutor:
    """Minimal AgentExecutor for testing."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def run(self, prompt: str, context: dict) -> dict:
        return {"answer": f"[{self._name}] done"}


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        executor = _StubExecutor("agent_1")
        reg.register(executor)
        assert reg.get("agent_1") is executor

    def test_get_nonexistent(self):
        reg = AgentRegistry()
        assert reg.get("ghost") is None

    def test_list_agents(self):
        reg = AgentRegistry()
        reg.register(_StubExecutor("a"))
        reg.register(_StubExecutor("b"))
        agents = reg.list_agents()
        assert set(agents) == {"a", "b"}

    def test_list_agents_empty(self):
        reg = AgentRegistry()
        assert reg.list_agents() == []

    def test_len(self):
        reg = AgentRegistry()
        assert len(reg) == 0
        reg.register(_StubExecutor("a"))
        assert len(reg) == 1

    def test_contains(self):
        reg = AgentRegistry()
        reg.register(_StubExecutor("a"))
        assert "a" in reg
        assert "b" not in reg

    def test_register_overwrites(self):
        reg = AgentRegistry()
        exec1 = _StubExecutor("a")
        exec2 = _StubExecutor("a")
        reg.register(exec1)
        reg.register(exec2)
        assert reg.get("a") is exec2

    def test_agent_executor_protocol(self):
        assert isinstance(_StubExecutor("x"), AgentExecutor)
