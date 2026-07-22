"""Hook 适配器 — AgentHook 13 点 → UnifiedHook 完整映射。

Adapted from llmwikify apps/chat/agent/unified/hook_adapter.py (MIT License).
Original: https://github.com/llmwikify/llmwikify

- UnifiedHook: 统一 hook 接口（16 个方法）
- AgentHookAdapter: 将 AgentHook 13 点桥接到 UnifiedHook
"""

from __future__ import annotations

from typing import Any

from .unified import UnifiedHook
from .composite import NoOpHook
from .context import AgentHookContext


class AgentHookAdapter(UnifiedHook):
    """AgentHook 13 点 → UnifiedHook 完整映射。

    将 AgentHook（composite.py）的 13 个 hook 方法
    映射到 UnifiedHook 的 16 个方法。缺失的映射用 pass 占位。
    """

    def __init__(self, hook: Any) -> None:
        self._hook = hook or NoOpHook()

    def wants_streaming(self) -> bool:
        return self._hook.wants_streaming()

    def before_iteration(self, ctx: Any) -> None:
        self._hook.before_iteration(self._to_hook_ctx(ctx))

    def on_reason_start(self, ctx: Any) -> None:
        pass  # AgentHook 没有直接对应

    def on_reason_end(self, ctx: Any, response: Any) -> None:
        self._hook.on_stream_end(self._to_hook_ctx(ctx), resuming=False)

    def on_stream(self, ctx: Any, delta: str) -> None:
        self._hook.on_stream(self._to_hook_ctx(ctx), delta)

    def emit_reasoning(self, ctx: Any, content: str) -> None:
        self._hook.emit_reasoning(self._to_hook_ctx(ctx), content)

    def emit_reasoning_end(self, ctx: Any) -> None:
        self._hook.emit_reasoning_end(self._to_hook_ctx(ctx))

    def on_act_start(self, ctx: Any) -> None:
        self._hook.before_execute_tools(self._to_hook_ctx(ctx))

    def on_act_end(self, ctx: Any, result: Any) -> None:
        pass  # after_tool_executed 由 ToolActor 内部调用

    def after_tool_executed(self, ctx: Any, tool_call: Any, result: Any) -> None:
        self._hook.after_tool_executed(self._to_hook_ctx(ctx), tool_call, result)

    def on_tool_error(self, ctx: Any, tool_call: Any, error: BaseException) -> None:
        self._hook.on_tool_error(self._to_hook_ctx(ctx), tool_call, error)

    def on_confirmation(self, ctx: Any, tool_call: Any) -> None:
        self._hook.on_confirmation(self._to_hook_ctx(ctx), tool_call)

    def on_observe(self, ctx: Any) -> None:
        pass  # AgentHook 没有直接对应

    def on_error(self, ctx: Any, error: BaseException) -> None:
        self._hook.on_error(self._to_hook_ctx(ctx), error)

    def finalize(self, ctx: Any, content: str | None) -> str | None:
        return self._hook.finalize_content(self._to_hook_ctx(ctx), content)

    def after_iteration(self, ctx: Any) -> None:
        self._hook.after_iteration(self._to_hook_ctx(ctx))

    def _to_hook_ctx(self, ctx: Any) -> AgentHookContext:
        """UnifiedContext → AgentHookContext 映射（17 字段）。"""
        return AgentHookContext(
            iteration=getattr(ctx, "iteration", 0),
            messages=getattr(ctx, "messages", []),
            response=getattr(ctx, "last_output", None),
            usage=dict(getattr(ctx, "usage", {})),
            tool_calls=[],
            tool_results=[],
            tool_events=[],
            streamed_content=False,
            streamed_reasoning=False,
            final_content=getattr(ctx, "final_content", None),
            stop_reason=getattr(ctx, "stop_reason", None),
            error=getattr(ctx, "error", None),
            observations=[],
            cancelled=getattr(getattr(ctx, "spec", None), "cancelled", False),
            paused=getattr(getattr(ctx, "spec", None), "paused", False),
            compacted_count=getattr(ctx, "compacted_count", 0),
            chars_saved=getattr(ctx, "total_compacted_chars_saved", 0),
        )
