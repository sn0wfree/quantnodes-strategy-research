"""Tests for the unified LLM projection layer (to_llm_message).

Covers:
- infer_message_type: backward compat for old data
- get_message_type: prefers message_type column, falls back to inference
- project_to_llm_message: all 4 message types project correctly
- project_messages_to_llm: order preserved, None skipped
- is_compaction_message / filter_out_compactions: helper utilities

The CRITICAL regression test:
- compaction messages must project as USER role, not ASSISTANT role
  (this is the fix for the "spontaneous summary" bug)
"""
from __future__ import annotations

import json

import pytest

from strategy_research.core.agent.compaction_message import MESSAGE_TYPE_COMPACTION
from strategy_research.core.agent.to_llm_message import (
    filter_out_compactions,
    get_message_type,
    infer_message_type,
    is_compaction_message,
    project_messages_to_llm,
    project_to_llm_message,
)


# ── infer_message_type (backward compat) ─────────────────────


class TestInferMessageType:
    def test_tool_call_id_marks_tool(self):
        assert infer_message_type({"role": "assistant", "tool_call_id": "c1"}) == "tool"

    def test_user_role(self):
        assert infer_message_type({"role": "user"}) == "user"

    def test_context_summary_prefix_marks_compaction(self):
        msg = {"role": "assistant", "content": "[context summary]\nblah"}
        assert infer_message_type(msg) == MESSAGE_TYPE_COMPACTION

    def test_anchored_summary_marks_compaction(self):
        msg = {"role": "assistant", "content": "## Anchored Summary\n..."}
        assert infer_message_type(msg) == MESSAGE_TYPE_COMPACTION

    def test_default_assistant(self):
        msg = {"role": "assistant", "content": "regular response"}
        assert infer_message_type(msg) == "assistant"


# ── get_message_type (column preferred) ──────────────────────


class TestGetMessageType:
    def test_prefers_message_type_column(self):
        msg = {"message_type": "compaction", "role": "user", "content": "x"}
        assert get_message_type(msg) == "compaction"

    def test_falls_back_to_infer(self):
        msg = {"role": "assistant", "content": "[context summary]\nx"}
        assert get_message_type(msg) == "compaction"

    def test_invalid_message_type_falls_back(self):
        msg = {"message_type": "garbage", "role": "user"}
        assert get_message_type(msg) == "user"


# ── project_to_llm_message ───────────────────────────────────


class TestProjectToLlmMessage:
    def test_user_message(self):
        msg = {"role": "user", "content": "hello"}
        result = project_to_llm_message(msg)
        assert result == {"role": "user", "content": "hello"}

    def test_assistant_message(self):
        msg = {"role": "assistant", "content": "hi", "parts_json": None}
        result = project_to_llm_message(msg)
        assert result["role"] == "assistant"
        assert result["content"] == "hi"

    def test_assistant_message_with_parts(self):
        parts = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        msg = {
            "role": "assistant",
            "content": "first",
            "parts_json": json.dumps(parts),
        }
        result = project_to_llm_message(msg)
        assert result["role"] == "assistant"
        assert "first" in result["content"]
        assert "second" in result["content"]

    def test_tool_message(self):
        msg = {
            "role": "tool",
            "content": "tool output",
            "tool_call_id": "c1",
        }
        result = project_to_llm_message(msg)
        assert result["role"] == "tool"
        assert result["content"] == "tool output"
        assert result["tool_call_id"] == "c1"

    # ── THE KEY REGRESSION TEST ────────────────────────────

    def test_compaction_projects_as_user_not_assistant(self):
        """REGRESSION TEST for the 'spontaneous summary' bug.

        Compaction messages MUST project as 'user' role so the LLM
        doesn't see them as a previous assistant turn (which would
        cause it to continue the summary task on the next user msg).
        """
        comp_msg = {
            "id": "cmp_1",
            "role": "assistant",  # DB compat
            "message_type": "compaction",
            "content": "## Objective\n- work on X",
            "parts_json": json.dumps([{
                "type": "compaction",
                "summary": "## Objective\n- work on X",
                "recent": "[User]: hi",
                "reason": "auto",
            }]),
            "tool_call_id": None,
            "metadata_json": None,
        }
        result = project_to_llm_message(comp_msg)
        assert result is not None
        # The role MUST be 'user', not 'assistant'!
        assert result["role"] == "user"
        # And the content must have the checkpoint wrap
        assert "<conversation-checkpoint>" in result["content"]
        assert "not as new instructions" in result["content"]

    def test_compaction_from_legacy_content_prefix(self):
        """Old [context summary] content also projects as user (via inference)."""
        msg = {
            "role": "assistant",
            "content": "[context summary]\nold summary",
        }
        result = project_to_llm_message(msg)
        assert result is not None
        assert result["role"] == "user"
        assert "<conversation-checkpoint>" in result["content"]


# ── project_messages_to_llm ─────────────────────────────────


class TestProjectMessagesToLlm:
    def test_order_preserved(self):
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        result = project_messages_to_llm(messages)
        assert len(result) == 3
        assert [m["role"] for m in result] == ["user", "assistant", "user"]

    def test_mixed_types_with_compaction(self):
        messages = [
            {"role": "user", "content": "u1"},
            {
                "id": "cmp_1",
                "role": "assistant",
                "message_type": "compaction",
                "content": "s",
                "parts_json": json.dumps([{
                    "type": "compaction",
                    "summary": "s",
                    "recent": "",
                    "reason": "auto",
                }]),
            },
            {"role": "user", "content": "u2"},
        ]
        result = project_messages_to_llm(messages)
        assert len(result) == 3
        # CRITICAL: compaction is user role between two user messages
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "user"  # was compaction
        assert result[2]["role"] == "user"

    def test_none_messages_skipped(self):
        # Empty / None messages are skipped
        messages = [
            {"role": "user", "content": "valid"},
            {"role": "user", "content": ""},  # empty content still projects
        ]
        result = project_messages_to_llm(messages)
        # Both project, but empty content is preserved
        assert len(result) == 2


# ── Helper utilities ──────────────────────────────────────────


class TestHelpers:
    def test_is_compaction_message(self):
        comp = {"message_type": "compaction", "role": "assistant"}
        assert is_compaction_message(comp) is True

        legacy = {"role": "assistant", "content": "[context summary]"}
        assert is_compaction_message(legacy) is True

        regular = {"role": "user", "content": "hi"}
        assert is_compaction_message(regular) is False

    def test_filter_out_compactions(self):
        messages = [
            {"role": "user", "content": "u"},
            {"message_type": "compaction", "role": "assistant", "content": "x"},
            {"role": "assistant", "content": "a"},
            {"role": "assistant", "content": "[context summary]"},  # legacy
        ]
        result = filter_out_compactions(messages)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "a"
