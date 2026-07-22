"""P2-b+P2-e End-to-End tests.

Tests for:
- AgentLoop hook integration
- Cross-session memory recall via ContextBuilder
- CLI session show/search/delete commands
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.core.agent.loop import AgentLoop, LoopResult
from strategy_research.core.agent.context import ContextBuilder
from strategy_research.core.agent.tools import ToolRegistry
from strategy_research.core.hooks.composite import CompositeHook, AgentHook
from strategy_research.core.hooks.context import AgentHookContext
from strategy_research.core.llm.config import LLMConfig
from strategy_research.core.memory.persistent import PersistentMemory


# ── Test Hooks ─────────────────────────────────────────────


class RecordingHook(AgentHook):
    """Hook that records all calls for verification."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    def before_run(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_run", [ctx.iteration]))

    def after_run(self, ctx: AgentHookContext, result) -> None:
        self.calls.append(("after_run", [ctx.iteration, result]))

    def before_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_iteration", [ctx.iteration]))

    def after_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append(("after_iteration", [ctx.iteration]))

    def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_execute_tools", [ctx.iteration]))

    def after_tool_executed(self, ctx, tool_call, result) -> None:
        self.calls.append(("after_tool_executed", [tool_call, result]))

    def on_tool_error(self, ctx, tool_call, error) -> None:
        self.calls.append(("on_tool_error", [tool_call, error]))

    def on_error(self, ctx, error) -> None:
        self.calls.append(("on_error", [error]))


# ── AgentLoop Hook Integration Tests ──────────────────────


class TestAgentLoopHookIntegration:
    """Test that AgentLoop correctly fires hook events."""

    def test_hooks_called_on_simple_run(self):
        hook = RecordingHook()
        composite = CompositeHook([hook])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=3,
        )
        loop.client = mock_client

        result = loop.run("test task")

        call_names = [c[0] for c in hook.calls]
        assert "before_run" in call_names
        assert "after_run" in call_names
        assert "before_iteration" in call_names
        assert "after_iteration" in call_names

    def test_hooks_fired_per_iteration(self):
        hook = RecordingHook()
        composite = CompositeHook([hook])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=5,
        )
        loop.client = mock_client

        result = loop.run("test task")

        iter_calls = [c for c in hook.calls if c[0] == "before_iteration"]
        assert len(iter_calls) == 1  # only 1 iteration before stop

    def test_no_hooks_no_crash(self):
        """AgentLoop works fine without hooks."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=None,
            max_iterations=3,
        )
        loop.client = mock_client

        result = loop.run("test task")
        assert result.success

    def test_hook_error_does_not_break_loop(self):
        """A failing hook should not crash the agent loop."""

        class BrokenHook(AgentHook):
            name = "broken"

            def before_iteration(self, ctx):
                raise RuntimeError("hook broken")

        composite = CompositeHook([BrokenHook()])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=3,
        )
        loop.client = mock_client

        # Should not raise
        result = loop.run("test task")
        assert result.success


# ── ContextBuilder Cross-Session Memory Tests ──────────────


class TestContextBuilderCrossSession:
    """Test ContextBuilder with SessionDB integration (P2-e)."""

    def test_recall_includes_session_results(self):
        mock_session_manager = MagicMock()
        mock_session_manager.search_messages.return_value = [
            {"role": "assistant", "content": "past strategy insight"},
            {"role": "user", "content": "what about momentum?"},
        ]

        config = LLMConfig(model="test", api_key="test")
        registry = ToolRegistry()

        builder = ContextBuilder(
            config=config,
            registry=registry,
            session_manager=mock_session_manager,
        )

        recalled = builder._recall_relevant("momentum strategy")
        assert "past strategy insight" in recalled
        assert "what about momentum?" in recalled
        assert "session:" in recalled  # tagged as session memory

    def test_recall_combines_persistent_and_session(self):
        mock_memory = MagicMock()
        entry = MagicMock()
        entry.title = "workspace memory"
        entry.description = "a note"
        mock_memory.find_relevant.return_value = [entry]
        mock_memory.snapshot = "snapshot"

        mock_session_manager = MagicMock()
        mock_session_manager.search_messages.return_value = [
            {"role": "assistant", "content": "session memory"},
        ]

        config = LLMConfig(model="test", api_key="test")
        registry = ToolRegistry()

        builder = ContextBuilder(
            config=config,
            registry=registry,
            memory=mock_memory,
            session_manager=mock_session_manager,
        )

        recalled = builder._recall_relevant("test query")
        assert "workspace memory" in recalled
        assert "session memory" in recalled

    def test_recall_session_error_handled(self):
        mock_session_manager = MagicMock()
        mock_session_manager.search_messages.side_effect = RuntimeError("DB error")

        config = LLMConfig(model="test", api_key="test")
        registry = ToolRegistry()

        builder = ContextBuilder(
            config=config,
            registry=registry,
            session_manager=mock_session_manager,
        )

        # Should not raise
        recalled = builder._recall_relevant("test query")
        assert isinstance(recalled, str)

    def test_recall_no_session_manager(self):
        config = LLMConfig(model="test", api_key="test")
        registry = ToolRegistry()

        builder = ContextBuilder(
            config=config,
            registry=registry,
            session_manager=None,
        )

        recalled = builder._recall_relevant("test query")
        assert recalled == ""


# ── CLI Session Commands Tests ─────────────────────────────


class TestCLISessionCommands:
    """Test CLI session show/search/delete commands."""

    def test_session_show_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "show", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_search_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "search", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_delete_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "delete", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_show_nonexistent(self):
        from strategy_research.cli import cmd_session_show
        args = argparse.Namespace(session_id="nonexistent_123")
        result = cmd_session_show(args)
        assert result == 1

    def test_session_delete_nonexistent(self):
        from strategy_research.cli import cmd_session_delete
        args = argparse.Namespace(session_id="nonexistent_123")
        result = cmd_session_delete(args)
        assert result == 1

    def test_session_search_empty(self):
        from strategy_research.cli import cmd_session_search
        args = argparse.Namespace(query="xyz_nonexistent_query_12345", limit=10)
        result = cmd_session_search(args)
        assert result == 0
