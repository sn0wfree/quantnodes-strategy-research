"""Tests for CompactConfig and compact_messages core functions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.core.agent.compact import (
    CompactConfig,
    _build_summary_prompt,
    _estimate_tokens,
    _fix_tool_pairs,
    _get_tool_name,
    _hard_truncate,
    _select_by_token_budget,
    _serialize_message,
    _smart_microcompact,
    _split_into_turns,
    compact_messages,
)


# ── CompactConfig defaults ────────────────────────────────────────


class TestCompactConfig:
    def test_defaults(self):
        cfg = CompactConfig()
        assert cfg.enabled is True
        assert cfg.microcompact_ratio == 0.5
        assert cfg.llm_summarize_ratio == 0.8
        assert cfg.hard_truncate_ratio == 0.9
        assert cfg.overflow_ratio == 0.95
        assert cfg.microcompact_tool_result_limit == 2000
        assert cfg.collapse_keep_recent == 4
        assert cfg.tail_turns == 2
        assert cfg.preserve_recent_tokens is None
        assert cfg.enable_incremental_summary is True

    def test_custom_values(self):
        cfg = CompactConfig(
            microcompact_ratio=0.6,
            llm_summarize_ratio=0.7,
            microcompact_tool_result_limit=3000,
            collapse_keep_recent=6,
            tail_turns=3,
            preserve_recent_tokens=10000,
        )
        assert cfg.microcompact_ratio == 0.6
        assert cfg.llm_summarize_ratio == 0.7
        assert cfg.microcompact_tool_result_limit == 3000
        assert cfg.collapse_keep_recent == 6
        assert cfg.tail_turns == 3
        assert cfg.preserve_recent_tokens == 10000

    def test_frozen(self):
        cfg = CompactConfig()
        with pytest.raises(AttributeError):
            cfg.microcompact_ratio = 0.9

    def test_tool_truncate_limits_default(self):
        cfg = CompactConfig()
        assert "read_file" in cfg.tool_truncate_limits
        assert "backtest_run" in cfg.tool_truncate_limits
        assert cfg.tool_truncate_limits["read_file"] == 3000

    def test_tool_truncate_limits_custom(self):
        limits = {"read_file": 5000, "custom_tool": 1000}
        cfg = CompactConfig(tool_truncate_limits=limits)
        assert cfg.tool_truncate_limits["read_file"] == 5000
        assert cfg.tool_truncate_limits["custom_tool"] == 1000


# ── Token estimation ──────────────────────────────────────────────


class TestEstimateTokens:
    def test_empty_messages(self):
        assert _estimate_tokens([]) >= 1

    def test_simple_text(self):
        msgs = [{"role": "user", "content": "hello world"}]
        tokens = _estimate_tokens(msgs)
        assert tokens > 0

    def test_with_tool_calls(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "test", "arguments": '{"arg": "val"}'}}
            ]}
        ]
        tokens = _estimate_tokens(msgs)
        assert tokens > 0


# ── L1: Smart Microcompact ───────────────────────────────────────


class TestSmartMicrocompact:
    def test_no_truncation_needed(self):
        msgs = [{"role": "tool", "content": "short output"}]
        cfg = CompactConfig(microcompact_tool_result_limit=2000)
        result, count = _smart_microcompact(msgs, cfg)
        assert count == 0
        assert result[0]["content"] == "short output"

    def test_truncation_applied(self):
        long_content = "x" * 3000
        msgs = [{"role": "tool", "content": long_content, "tool_call_id": "c1"}]
        cfg = CompactConfig(microcompact_tool_result_limit=2000)
        result, count = _smart_microcompact(msgs, cfg)
        assert count == 1
        assert len(result[0]["content"]) < 3000
        assert "truncated" in result[0]["content"]

    def test_head_tail_truncation(self):
        content = "A" * 1000 + "MIDDLE" + "Z" * 1000
        msgs = [{"role": "tool", "content": content, "tool_call_id": "c1"}]
        cfg = CompactConfig(microcompact_tool_result_limit=200)
        result, count = _smart_microcompact(msgs, cfg)
        assert count == 1
        truncated = result[0]["content"]
        # Head 60% + tail 40% of 200 = 120 head + 80 tail
        assert truncated.startswith("A" * 120)
        assert truncated.endswith("Z" * 80)

    def test_skip_error_messages(self):
        error_content = "Error: something went wrong"
        msgs = [{"role": "tool", "content": error_content}]
        cfg = CompactConfig(microcompact_tool_result_limit=10)
        result, count = _smart_microcompact(msgs, cfg)
        assert count == 0

    def test_skip_recent_tool_outputs(self):
        msgs = [
            {"role": "tool", "content": "x" * 3000, "tool_call_id": "c1"},
            {"role": "tool", "content": "y" * 3000, "tool_call_id": "c2"},
            {"role": "tool", "content": "z" * 3000, "tool_call_id": "c3"},
            {"role": "tool", "content": "w" * 3000, "tool_call_id": "c4"},
        ]
        cfg = CompactConfig(microcompact_tool_result_limit=2000, collapse_keep_recent=2)
        result, count = _smart_microcompact(msgs, cfg)
        # Last 2 should be protected
        assert count == 2

    def test_per_tool_limit(self):
        content = "x" * 3500
        # Set up messages so tool name can be found
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": content, "tool_call_id": "c1"},
        ]
        cfg = CompactConfig(
            microcompact_tool_result_limit=2000,
            tool_truncate_limits={"read_file": 4000},
        )
        result, count = _smart_microcompact(msgs, cfg)
        assert count == 0  # 3500 < 4000 limit for read_file


# ── Tool name resolution ──────────────────────────────────────────


class TestGetToolName:
    def test_find_tool_name(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "output", "tool_call_id": "c1"},
        ]
        assert _get_tool_name(msgs, 1) == "read_file"

    def test_find_tool_name_nested_function(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "output", "tool_call_id": "c1"},
        ]
        assert _get_tool_name(msgs, 1) == "test"

    def test_default_tool_name(self):
        msgs = [{"role": "tool", "content": "output", "tool_call_id": "unknown"}]
        assert _get_tool_name(msgs, 0) == "_default"


# ── L3: Hard Truncate ────────────────────────────────────────────


class TestHardTruncate:
    def test_keeps_system_and_recent(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = _hard_truncate(msgs, keep_recent=2)
        assert len(result) == 3  # system + 2 recent
        assert result[0]["content"] == "sys"
        assert result[-1]["content"] == "a2"

    def test_no_system(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = _hard_truncate(msgs, keep_recent=1)
        assert len(result) == 1
        assert result[0]["content"] == "u2"


# ── Fix Tool Pairs ───────────────────────────────────────────────


class TestFixToolPairs:
    def test_no_orphans(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "result", "tool_call_id": "c1"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 2

    def test_remove_orphan_result(self):
        msgs = [
            {"role": "tool", "content": "orphan", "tool_call_id": "c1"},
        ]
        result = _fix_tool_pairs(msgs)
        assert len(result) == 0

    def test_remove_orphan_call(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "test", "arguments": "{}"}}
            ]},
            {"role": "assistant", "content": "no tools"},
        ]
        result = _fix_tool_pairs(msgs)
        # Assistant with only orphaned tool_calls and no content is dropped
        assert len(result) == 1
        assert result[0]["content"] == "no tools"


# ── Token budget selection ────────────────────────────────────────


class TestSelectByTokenBudget:
    def test_returns_head_and_recent(self):
        msgs = [
            {"role": "user", "content": "u1 " * 100},
            {"role": "assistant", "content": "a1 " * 100},
            {"role": "user", "content": "u2 " * 100},
            {"role": "assistant", "content": "a2 " * 100},
            {"role": "user", "content": "u3 " * 100},
            {"role": "assistant", "content": "a3 " * 100},
        ]
        cfg = CompactConfig(tail_turns=1, preserve_recent_tokens=500)
        head, recent = _select_by_token_budget(msgs, cfg, None)
        assert len(head) > 0
        assert len(recent) > 0
        assert len(head) + len(recent) == len(msgs)

    def test_empty_messages(self):
        cfg = CompactConfig()
        head, recent = _select_by_token_budget([], cfg, None)
        assert head == []
        assert recent == []


# ── Split into turns ──────────────────────────────────────────────


class TestSplitIntoTurns:
    def test_basic(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        turns = _split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 2
        assert len(turns[1]) == 2

    def test_trailing_user(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        turns = _split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[1]) == 1


# ── Serialization ─────────────────────────────────────────────────


class TestSerializeMessage:
    def test_user_message(self):
        msg = {"role": "user", "content": "hello"}
        assert _serialize_message(msg) == "[User]: hello"

    def test_assistant_message(self):
        msg = {"role": "assistant", "content": "response"}
        assert _serialize_message(msg) == "[Assistant]: response"

    def test_tool_call(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "test", "arguments": '{"k":"v"}'}}],
        }
        result = _serialize_message(msg)
        assert "[ToolCall]: test" in result

    def test_tool_result(self):
        msg = {"role": "tool", "content": "output", "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "[ToolResult:c1]:" in result
        assert "output" in result

    def test_tool_result_truncation(self):
        msg = {"role": "tool", "content": "x" * 3000, "tool_call_id": "c1"}
        result = _serialize_message(msg)
        assert "truncated" in result
        assert len(result) < 3000


# ── Summary prompt ────────────────────────────────────────────────


class TestBuildSummaryPrompt:
    def test_first_summary(self):
        prompt = _build_summary_prompt("conversation", None, "template")
        assert "Create a new anchored summary" in prompt
        assert "conversation" in prompt
        assert "template" in prompt

    def test_incremental_summary(self):
        prompt = _build_summary_prompt("conversation", "previous", "template")
        assert "Update the anchored summary" in prompt
        assert "<previous-summary>" in prompt
        assert "previous" in prompt


# ── compact_messages integration ──────────────────────────────────


class TestCompactMessages:
    def test_no_compaction_below_threshold(self):
        msgs = [{"role": "user", "content": "hello"}]
        cfg = CompactConfig()
        result, layers = compact_messages(msgs, cfg, threshold_tokens=8000)
        assert layers == []
        assert len(result) == 1

    def test_disabled(self):
        msgs = [{"role": "user", "content": "x" * 10000}]
        cfg = CompactConfig(enabled=False)
        result, layers = compact_messages(msgs, cfg, threshold_tokens=100)
        assert layers == []

    def test_microcompact_only(self):
        # Create messages with large tool output to trigger L1
        msgs = [
            {"role": "user", "content": "hello world, this is a test message with enough content to exceed the threshold"},
            {"role": "assistant", "content": "hi there, I will help you with this test"},
            {"role": "tool", "content": "x" * 5000, "tool_call_id": "c1"},
        ]
        cfg = CompactConfig(microcompact_tool_result_limit=2000)
        result, layers = compact_messages(
            msgs, cfg, threshold_tokens=100, llm_client=None,
        )
        assert any("microcompact" in l for l in layers)

    def test_hard_truncate(self):
        # Create many messages to exceed threshold
        msgs = [{"role": "user", "content": f"message {i} " * 20} for i in range(20)]
        cfg = CompactConfig(hard_truncate_ratio=0.0, collapse_keep_recent=3)
        result, layers = compact_messages(msgs, cfg, threshold_tokens=100)
        assert any("truncate" in l for l in layers)
        assert len(result) <= 3 + 1  # keep_recent + any system msgs


# ── LLMConfig integration ────────────────────────────────────────


class TestLLMConfigCompactConfig:
    def test_compact_config_in_llm_config(self):
        from strategy_research.core.llm.config import LLMConfig
        cfg = LLMConfig(compact_config=CompactConfig(microcompact_ratio=0.6))
        assert cfg.compact_config is not None
        assert cfg.compact_config.microcompact_ratio == 0.6

    def test_default_none(self):
        from strategy_research.core.llm.config import LLMConfig
        cfg = LLMConfig()
        assert cfg.compact_config is None
