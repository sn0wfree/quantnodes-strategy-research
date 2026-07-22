"""Tests for hooks/unified.py — UnifiedContext dataclass + UnifiedHook."""

from __future__ import annotations

import pytest

from strategy_research.core.hooks.unified import UnifiedContext, UnifiedHook


# ============================================================
# UnifiedContext dataclass
# ============================================================


class TestUnifiedContext:
    def test_defaults(self):
        ctx = UnifiedContext()
        assert ctx.session_id == ""
        assert ctx.iteration == 0
        assert ctx.prompt == ""
        assert ctx.metadata == {}

    def test_with_session_id(self):
        ctx = UnifiedContext(session_id="abc123")
        assert ctx.session_id == "abc123"

    def test_with_iteration(self):
        ctx = UnifiedContext(iteration=5)
        assert ctx.iteration == 5

    def test_with_prompt(self):
        ctx = UnifiedContext(prompt="hello world")
        assert ctx.prompt == "hello world"

    def test_with_metadata(self):
        ctx = UnifiedContext(metadata={"key": "value"})
        assert ctx.metadata == {"key": "value"}

    def test_metadata_default_is_independent(self):
        # Each instance should get its own dict (no mutable default sharing)
        ctx1 = UnifiedContext()
        ctx2 = UnifiedContext()
        ctx1.metadata["key"] = "value"
        assert "key" not in ctx2.metadata

    def test_full_construction(self):
        ctx = UnifiedContext(
            session_id="sess_001",
            iteration=10,
            prompt="test prompt",
            metadata={"user": "alice"},
        )
        assert ctx.session_id == "sess_001"
        assert ctx.iteration == 10
        assert ctx.prompt == "test prompt"
        assert ctx.metadata["user"] == "alice"

    def test_mutable_fields(self):
        ctx = UnifiedContext()
        ctx.iteration = 100
        ctx.metadata["new"] = "data"
        assert ctx.iteration == 100
        assert ctx.metadata["new"] == "data"

    def test_equality(self):
        ctx1 = UnifiedContext(session_id="x", iteration=1)
        ctx2 = UnifiedContext(session_id="x", iteration=1)
        assert ctx1 == ctx2

    def test_inequality(self):
        ctx1 = UnifiedContext(session_id="x", iteration=1)
        ctx2 = UnifiedContext(session_id="y", iteration=1)
        assert ctx1 != ctx2


# ============================================================
# UnifiedHook default no-ops
# ============================================================


class TestUnifiedHookDefaults:
    def test_wants_streaming_default_false(self):
        hook = UnifiedHook()
        assert hook.wants_streaming() is False

    def test_before_iteration_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        # Should not raise
        hook.before_iteration(ctx)

    def test_on_reason_start_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_reason_start(ctx)

    def test_on_reason_end_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_reason_end(ctx, response="some response")

    def test_on_stream_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_stream(ctx, delta="hello")

    def test_emit_reasoning_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.emit_reasoning(ctx, content="thinking...")

    def test_emit_reasoning_end_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.emit_reasoning_end(ctx)

    def test_on_act_start_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_act_start(ctx)

    def test_on_act_end_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_act_end(ctx, result={"status": "ok"})

    def test_after_tool_executed_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.after_tool_executed(ctx, tool_call={"name": "test"}, result={"ok": True})

    def test_on_tool_error_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_tool_error(ctx, tool_call={"name": "test"}, error=ValueError("oops"))

    def test_on_confirmation_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_confirmation(ctx, tool_call={"name": "test"})

    def test_on_observe_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_observe(ctx)

    def test_on_error_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.on_error(ctx, error=ValueError("test error"))

    def test_after_iteration_noop(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        hook.after_iteration(ctx)

    def test_finalize_returns_content(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        result = hook.finalize(ctx, content="my content")
        assert result == "my content"

    def test_finalize_with_none(self):
        hook = UnifiedHook()
        ctx = UnifiedContext()
        result = hook.finalize(ctx, content=None)
        assert result is None


# ============================================================
# UnifiedHook subclass overrides
# ============================================================


class TestUnifiedHookSubclass:
    def test_subclass_can_override(self):
        events = []

        class TrackingHook(UnifiedHook):
            def before_iteration(self, ctx):
                events.append(("before", ctx.iteration))

            def on_stream(self, ctx, delta):
                events.append(("stream", delta))

            def finalize(self, ctx, content):
                return f"FINALIZED:{content}"

        hook = TrackingHook()
        ctx = UnifiedContext(iteration=1, session_id="s1")

        hook.before_iteration(ctx)
        assert events == [("before", 1)]

        hook.on_stream(ctx, "chunk1")
        assert events == [("before", 1), ("stream", "chunk1")]

        result = hook.finalize(ctx, "test")
        assert result == "FINALIZED:test"

    def test_subclass_inherits_other_events(self):
        class MyHook(UnifiedHook):
            def before_iteration(self, ctx):
                pass

        hook = MyHook()
        ctx = UnifiedContext()
        # Other methods should still be no-ops
        hook.on_reason_start(ctx)
        hook.after_iteration(ctx)


# ============================================================
# Type safety
# ============================================================


class TestUnifiedContextTypeSafety:
    def test_unified_context_is_dataclass(self):
        from dataclasses import is_dataclass
        assert is_dataclass(UnifiedContext)

    def test_unified_context_has_typed_fields(self):
        from typing import get_type_hints
        hints = get_type_hints(UnifiedContext)
        assert hints["session_id"] is str
        assert hints["iteration"] is int
        assert hints["prompt"] is str

    def test_can_be_used_as_hook_arg(self):
        hook = UnifiedHook()
        ctx = UnifiedContext(session_id="abc", iteration=1, prompt="p")
        # These should not raise type errors
        hook.before_iteration(ctx)
        hook.after_iteration(ctx)
        hook.on_observe(ctx)