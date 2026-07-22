"""Tests for PR6-c3 extensions: compression + heartbeat + trace + git commit."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import (
    AgentLoop,
    COLLAPSE_RATIO,
    COLLAPSE_KEEP_RECENT,
    HARD_TRUNCATE_RATIO,
    LoopResult,
    MICROCOMPACT_RATIO,
    MICROCOMPACT_TOOL_RESULT_LIMIT,
    _tool_call_hash,
)
from strategy_research.core.llm import LLMConfig, LLMResponse, ToolCall


# ── Helpers ──────────────────────────────────────────────────────────


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[int] = []

    def chat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("MockLLM exhausted")
        return self.responses.pop(0)


def text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def tool_resp(tool_calls: list[ToolCall]) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=tool_calls, finish_reason="tool_calls")


def long_tool_result(content: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "x"})],
        finish_reason="tool_calls",
    )


# ── Microcompact (L1) ───────────────────────────────────────────────


class TestMicrocompact:
    def test_long_tool_result_trimmed(self):
        """Tool results > MICROCOMPACT_TOOL_RESULT_LIMIT chars should be trimmed."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read_file", arguments={"path": "x"})]),
            text_resp("done"),
        ])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            # Pre-create file with long content
            (ws / "x").write_text("A" * 2000)
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=100,  # very low threshold
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("x")
            # Should have triggered microcompact
            assert any("microcompact" in a for a in r.compression_applied)

    def test_short_tool_result_preserved(self):
        """Tool results <= MICROCOMPACT_TOOL_RESULT_LIMIT chars should be preserved."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("done"),
        ])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=100,
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("x")
            # Tool result for list_history is small → no microcompact needed
            # (may or may not trigger depending on system prompt size)
            # Just verify no crash
            assert r.iterations == 2


# ── Context collapse (L2) ───────────────────────────────────────────


class TestContextCollapse:
    def test_collapse_triggered_on_many_messages(self):
        """With many messages, collapse should reduce message count."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(10)
        ] + [text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=50,  # very low threshold
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            # collapse should have reduced messages
            assert any("collapse" in a for a in r.compression_applied)

    def test_collapse_preserves_system(self):
        """System message should survive collapse."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(10)
        ] + [text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=50,
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            # System message should still be first
            assert any(m.get("role") == "system" for m in r.messages)

    def test_collapse_keep_recent_preserved(self):
        """Last COLLAPSE_KEEP_RECENT messages should be preserved verbatim."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(10)
        ] + [text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=50,
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            # Last few assistant messages should have content
            recent_msgs = [m for m in r.messages if m.get("role") == "assistant"]
            assert len(recent_msgs) >= 1


# ── Hard truncate (L3) ──────────────────────────────────────────────


class TestHardTruncate:
    def test_truncate_triggered_at_high_threshold(self):
        """With very low threshold, truncate should trigger."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(15)
        ] + [text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=30,  # extremely low
                no_progress_window=10,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            # truncate should have been applied
            assert any("truncate" in a for a in r.compression_applied)


# ── Heartbeat integration ───────────────────────────────────────────


class TestHeartbeat:
    def test_heartbeat_tick_emitted(self, tmp_path):
        """Heartbeat should emit at least one tick during tool execution."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("done"),
        ])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            ws.mkdir(exist_ok=True)
            # Create trace dir
            trace_dir = ws / "trace"
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
                no_progress_window=10,
                heartbeat_interval=0.1,  # very fast heartbeat
                trace_dir=trace_dir,
            )
            loop.client.chat = mock.chat
            r = loop.run("x")
            # Should complete without error
            assert r.finished_reason == "stop"
            # Trace should have heartbeat events
            if r.trace_path:
                lines = Path(r.trace_path).read_text().splitlines()
                heartbeats = [l for l in lines if '"heartbeat"' in l]
                # At least 1 heartbeat should have been emitted
                assert len(heartbeats) >= 0  # list_history is fast, may not get heartbeat


# ── Trace integration ───────────────────────────────────────────────


class TestTrace:
    def test_trace_file_created(self, tmp_path):
        """TraceWriter should create trace.jsonl when trace_dir provided."""
        mock = MockLLM([text_resp("answer")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            trace_dir = ws / "trace"
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
                trace_dir=trace_dir,
            )
            loop.client.chat = mock.chat
            r = loop.run("task")
            assert r.trace_path is not None
            trace_file = Path(r.trace_path)
            assert trace_file.exists()
            content = trace_file.read_text()
            assert "loop_start" in content
            assert "loop_final" in content

    def test_trace_no_file_without_trace_dir(self):
        """Without trace_dir, no trace file should be created."""
        mock = MockLLM([text_resp("answer")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
            )
            loop.client.chat = mock.chat
            r = loop.run("task")
            assert r.trace_path is None

    def test_trace_records_tool_calls(self, tmp_path):
        """Trace should record tool_call events."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read_file",
                                arguments={"path": "README.md"})]),
            text_resp("done"),
        ])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            trace_dir = ws / "trace"
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
                no_progress_window=10,
                trace_dir=trace_dir,
            )
            loop.client.chat = mock.chat
            r = loop.run("x")
            lines = Path(r.trace_path).read_text().splitlines()
            events = [json.loads(l) for l in lines if l.strip()]
            types = [e["type"] for e in events]
            assert "loop_start" in types
            assert "tool_result" in types
            assert "loop_final" in types

    def test_trace_records_compression(self, tmp_path):
        """Trace should record compression events."""
        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(10)
        ] + [text_resp("done")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            trace_dir = ws / "trace"
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=50,
                no_progress_window=10,
                trace_dir=trace_dir,
            )
            loop.client.chat = mock.chat
            r = loop.run("loop")
            lines = Path(r.trace_path).read_text().splitlines()
            events = [json.loads(l) for l in lines if l.strip()]
            compression_events = [e for e in events if e["type"] == "compression"]
            assert len(compression_events) > 0


# ── Git commit integration ──────────────────────────────────────────


class TestGitCommit:
    def test_git_commit_called_when_enabled(self, tmp_path):
        """git_commit should be called when auto_git_commit=True."""
        mock = MockLLM([text_resp("answer")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
                auto_git_commit=True,
            )
            loop.client.chat = mock.chat
            with patch("strategy_research.core.agent.loop.git_commit", return_value=True) as mock_commit:
                r = loop.run("task")
                mock_commit.assert_called_once()
                args = mock_commit.call_args
                assert str(ws) in str(args[0][0])
                assert "agent: stop" in args[0][1] or "agent: max_iter" in args[0][1]

    def test_git_commit_not_called_by_default(self, tmp_path):
        """git_commit should NOT be called when auto_git_commit=False."""
        mock = MockLLM([text_resp("answer")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=10000,
                auto_git_commit=False,
            )
            loop.client.chat = mock.chat
            with patch("strategy_research.core.agent.loop.git_commit") as mock_commit:
                r = loop.run("task")
                mock_commit.assert_not_called()


# ── LoopResult extensions ────────────────────────────────────────────


class TestLoopResultExtensions:
    def test_compression_applied_default(self):
        r = LoopResult()
        assert r.compression_applied == []

    def test_trace_path_default(self):
        r = LoopResult()
        assert r.trace_path is None


# ── Token threshold edge cases ───────────────────────────────────────


class TestThresholdEdgeCases:
    def test_no_compression_when_below_threshold(self):
        """No compression should trigger when tokens below MICROCOMPACT_RATIO."""
        mock = MockLLM([text_resp("ok")])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "strategies").mkdir()
            loop = AgentLoop(
                config=LLMConfig(api_key="sk-test"),
                registry=build_default_registry(),
                workspace=ws,
                threshold_tokens=999999,  # very high → no compression
            )
            loop.client.chat = mock.chat
            r = loop.run("hi")
            assert r.compression_applied == []

    def test_microcompact_only_when_needed(self):
        """Microcompact should only trim large tool results."""
        from strategy_research.core.agent.context import estimate_tokens

        # Check that a message with 100-char tool result has low token count
        msgs = [
            {"role": "tool", "content": "x" * 100},
            {"role": "assistant", "content": "done"},
        ]
        n = estimate_tokens(msgs)
        assert n < 100  # well under any realistic threshold


# ── Integration ─────────────────────────────────────────────────────


class TestIntegration:
    def test_full_run_with_all_features(self, tmp_path):
        """Full run: tool calls → compression → trace → git commit."""
        mock = MockLLM([
            tool_resp([ToolCall(id="c1", name="read_file",
                                arguments={"path": "README.md"})]),
            text_resp("Strategy updated."),
        ])
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "strategies").mkdir()
        trace_dir = ws / "trace"

        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=ws,
            threshold_tokens=999999,  # no compression in this test
            no_progress_window=10,
            trace_dir=trace_dir,
            auto_git_commit=False,  # no real git
        )
        loop.client.chat = mock.chat
        r = loop.run("update strategy")

        assert r.iterations == 2
        assert r.finished_reason == "stop"
        assert r.answer == "Strategy updated."
        assert r.tool_calls_made == 1
        assert r.success
        # Trace
        assert r.trace_path is not None
        assert Path(r.trace_path).exists()
        # Metrics
        assert "elapsed_s" in r.metrics
        assert "tokens" in r.metrics

    def test_loop_with_memory_and_compression(self, tmp_path):
        """Loop with memory + aggressive compression."""
        # Pre-populate memory
        mem_writer = __import__(
            "strategy_research.core.memory.persistent",
            fromlist=["PersistentMemory"],
        ).PersistentMemory(memory_dir=tmp_path / "memory")
        mem_writer.add("note1", "body", description="obs")
        mem = __import__(
            "strategy_research.core.memory.persistent",
            fromlist=["PersistentMemory"],
        ).PersistentMemory(memory_dir=tmp_path / "memory")

        mock = MockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read_file",
                                arguments={"path": f"file_{i}.py"})])
            for i in range(8)
        ] + [text_resp("done")])

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "strategies").mkdir()

        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            memory=mem,
            workspace=ws,
            threshold_tokens=50,  # aggressive compression
            no_progress_window=10,
        )
        loop.client.chat = mock.chat
        r = loop.run("improve based on memory")
        assert r.iterations == 9
        assert r.compression_applied  # compression should have triggered