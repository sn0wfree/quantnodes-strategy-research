"""Tests for Stage A: assistant_message event + non-streaming content delivery.

Verifies that:
1. _handle_stop / _handle_max_iter / _check_no_progress each emit an
   ``assistant_message`` event with the final content BEFORE ``iter_end``.
2. The TUI App's ``_finalize_assistant_message`` writes content into the
   TranscriptView as a foldable StreamingText folder.
3. _run_agent_loop does NOT call ``append_done()`` redundantly
   (the "Done.×2" bug fix).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.llm import LLMConfig, LLMResponse, ToolCall


# ---------------------------------------------------------------- helpers


class EventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))

    def types(self) -> list[str]:
        return [e[0] for e in self.events]

    def of_type(self, t: str) -> list[dict[str, Any]]:
        return [d for et, d in self.events if et == t]


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.responses.pop(0)


def _make_loop(sink, *, max_iterations=1, stream_mode=False, no_progress_window=10):
    cfg = mock.MagicMock()
    cfg.model = "fake-model"
    cfg.temperature = 0.7
    workspace = Path(tempfile.mkdtemp())
    return AgentLoop(
        config=cfg,
        registry=build_default_registry(),
        workspace=workspace,
        on_event=sink,
        max_iterations=max_iterations,
        no_progress_window=no_progress_window,
        stream_mode=stream_mode,
    )


# ---------------------------------------------------------------- Stage A1


class TestAssistantMessageEmission:
    """A1: _handle_stop emits assistant_message BEFORE iter_end."""

    def test_stop_path_emits_assistant_message_with_content(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.chat = MockLLM([
            LLMResponse(content="策略名称: A股动量策略", tool_calls=[], finish_reason="stop"),
        ]).chat
        result = loop.run("分析A股")
        types = sink.types()
        assert "assistant_message" in types, f"missing assistant_message; got {types}"
        idx_msg = types.index("assistant_message")
        idx_end = types.index("iter_end")
        assert idx_msg < idx_end, "assistant_message must come BEFORE iter_end"
        assert sink.of_type("assistant_message")[0]["content"] == "策略名称: A股动量策略"
        assert result.answer == "策略名称: A股动量策略"
        assert result.finished_reason == "stop"

    def test_max_iter_path_emits_assistant_message(self):
        sink = EventSink()
        loop = _make_loop(sink, max_iterations=2)
        loop.client.chat = MockLLM([
            LLMResponse(content=None, tool_calls=[
                ToolCall(id="c1", name="read_file", arguments={"path": "x"}),
            ], finish_reason="tool_calls"),
            LLMResponse(content=None, tool_calls=[
                ToolCall(id="c2", name="read_file", arguments={"path": "y"}),
            ], finish_reason="tool_calls"),
        ]).chat
        result = loop.run("endless")
        types = sink.types()
        assert "assistant_message" in types
        am = sink.of_type("assistant_message")[0]
        assert "max_iterations" in am["content"]
        assert result.finished_reason == "max_iter"

    def test_no_progress_path_emits_assistant_message(self):
        sink = EventSink()
        loop = _make_loop(sink, max_iterations=10, no_progress_window=3)
        loop.client.chat = MockLLM([
            LLMResponse(content=None, tool_calls=[
                ToolCall(id=f"c{i}", name="list_history", arguments={}),
            ], finish_reason="tool_calls")
            for i in range(10)
        ]).chat
        result = loop.run("loop")
        types = sink.types()
        assert "assistant_message" in types
        am = sink.of_type("assistant_message")[0]
        assert "No progress" in am["content"] or am["content"] != ""
        assert result.finished_reason == "no_progress"

    def test_assistant_message_emitted_exactly_once_per_turn(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.chat = MockLLM([
            LLMResponse(content="done", tool_calls=[], finish_reason="stop"),
        ]).chat
        loop.run("once")
        am_events = sink.of_type("assistant_message")
        assert len(am_events) == 1, f"expected 1 assistant_message, got {len(am_events)}"

    def test_async_arun_also_emits_assistant_message(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.achat = MockLLM([
            LLMResponse(content="async answer", tool_calls=[], finish_reason="stop"),
        ]).achat
        result = asyncio.run(loop.arun("async task"))
        types = sink.types()
        assert "assistant_message" in types
        assert sink.of_type("assistant_message")[0]["content"] == "async answer"
        assert result.answer == "async answer"


# ---------------------------------------------------------------- Stage A2


class TestFinalizeAssistantMessage:
    """A2: route_agent_event writes assistant_message as a folder."""

    def test_finalize_creates_folder_when_no_streamer(self):
        """Non-streaming path: begin → update → end → one folder."""
        from strategy_research.cli.tui.widgets.streaming_text import StreamingText

        captured = []

        class FakeTV:
            def __init__(self):
                self._streamer = None
                self._stream_baseline = None
                self._folders = []
                self._fold_baselines = []
                self._fold_line_counts = []

            def begin_streaming(self):
                self._stream_baseline = 0
                self._streamer = StreamingText()
                self._streamer.start()
                captured.append(("begin",))

            def end_streaming(self, suffix=""):
                captured.append(("end",))
                self._folders.append(self._streamer)
                self._streamer = None
                self._stream_baseline = None

        tv = FakeTV()
        # Inject
        app = mock.MagicMock()
        app.query_one.return_value = tv

        from strategy_research.cli.tui.app import ResearchApp
        ResearchApp._finalize_assistant_message(app, "策略名称: A股动量策略")

        assert ("begin",) in captured
        assert ("end",) in captured
        assert len(tv._folders) == 1
        assert tv._folders[0].full_text == "策略名称: A股动量策略"

    def test_finalize_with_active_streamer_only_ends(self):
        """Streaming path: streamer has accumulated text → just end."""
        from strategy_research.cli.tui.widgets.streaming_text import StreamingText

        class FakeTV:
            def __init__(self):
                self._streamer = StreamingText()
                self._streamer.start()
                self._streamer.append_delta("partial ")
                self._stream_baseline = 0
                self._folders = []

            def begin_streaming(self):
                raise AssertionError("should not begin again")

            def end_streaming(self, suffix=""):
                self._folders.append(self._streamer)
                self._streamer = None

        tv = FakeTV()
        app = mock.MagicMock()
        app.query_one.return_value = tv

        from strategy_research.cli.tui.app import ResearchApp
        ResearchApp._finalize_assistant_message(app, "partial full")

        assert len(tv._folders) == 1
        # Streaming accumulation was overridden by full content — this is
        # the expected behaviour: ``assistant_message`` carries the
        # authoritative final text.
        assert tv._folders[0].full_text == "partial full"

    def test_finalize_handles_empty_content(self):
        """Empty content shouldn't crash; emits an empty folder."""
        from strategy_research.cli.tui.widgets.streaming_text import StreamingText

        class FakeTV:
            def __init__(self):
                self._streamer = None
                self._stream_baseline = None
                self._folders = []
                self._fold_line_counts = []

            def begin_streaming(self):
                self._stream_baseline = 0
                self._streamer = StreamingText()
                self._streamer.start()

            def end_streaming(self, suffix=""):
                if self._streamer is not None:
                    self._folders.append(self._streamer)
                    self._streamer = None

        tv = FakeTV()
        app = mock.MagicMock()
        app.query_one.return_value = tv

        from strategy_research.cli.tui.app import ResearchApp
        ResearchApp._finalize_assistant_message(app, "")

        assert len(tv._folders) == 1
        assert tv._folders[0].full_text == ""


# ---------------------------------------------------------------- Stage A3


class TestNoRedundantDone:
    """A3: _run_agent_loop must NOT call append_done() (iter_end handles it)."""

    def test_session_run_agent_loop_does_not_call_append_done(self):
        """Verify the source: _run_agent_loop no longer contains append_done."""
        import inspect
        from strategy_research.cli.tui.session import ChatSession

        src = inspect.getsource(ChatSession._run_agent_loop)
        # The fix removed the redundant append_done() call.
        # Defensive close (end_streaming) may still be present.
        assert "tv.append_done" not in src, (
            "_run_agent_loop must not call append_done() — iter_end route handler is canonical"
        )

    def test_route_agent_event_iter_end_calls_append_done(self):
        """Verify the canonical Done. marker source: route_agent_event iter_end."""
        import inspect
        from strategy_research.cli.tui.app import ResearchApp

        src = inspect.getsource(ResearchApp.route_agent_event)
        # iter_end branch should call tv.append_done()
        iter_end_block = src.split('event_type == "iter_end"')[1].split("elif")[0]
        assert "append_done" in iter_end_block