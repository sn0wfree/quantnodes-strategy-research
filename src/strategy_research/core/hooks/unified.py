"""UnifiedHook — 统一 hook 接口.

Adapted from llmwikify kernel/agent/hook.py (MIT License).
Original: https://github.com/llmwikify/llmwikify

所有 mode 共用。比 AgentHook 13 点更通用，适用于 Chat/Codegen/Research。
AgentHook 通过 AgentHookAdapter 桥接到此接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class UnifiedContext:
    """Hook execution context passed to all UnifiedHook events.

    Provides typed access to common context fields used across chat,
    codegen, and research modes. Subclasses or instances can extend with
    mode-specific fields as needed.
    """

    session_id: str = ""
    iteration: int = 0
    prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class _ContextLike(Protocol):
    """Minimal context protocol — anything with session_id is a context."""

    session_id: str


# Backward-compatible alias: UnifiedContext used to be Any; now it's a
# proper dataclass but accepts any object with .session_id via _ContextLike.
UnifiedContext = UnifiedContext


class UnifiedHook:
    """统一 hook 接口 — 所有 mode 共用。

    16 个事件点（默认全部 no-op）：
    - wants_streaming / before_iteration / after_iteration
    - on_reason_start / on_reason_end / on_stream
    - emit_reasoning / emit_reasoning_end
    - on_act_start / on_act_end
    - after_tool_executed / on_tool_error / on_confirmation
    - on_observe / on_error
    - finalize
    """

    def wants_streaming(self) -> bool:
        return False

    def before_iteration(self, ctx: UnifiedContext) -> None:
        pass

    def on_reason_start(self, ctx: UnifiedContext) -> None:
        pass

    def on_reason_end(self, ctx: UnifiedContext, response: Any) -> None:
        pass

    def on_stream(self, ctx: UnifiedContext, delta: str) -> None:
        pass

    def emit_reasoning(self, ctx: UnifiedContext, content: str) -> None:
        pass

    def emit_reasoning_end(self, ctx: UnifiedContext) -> None:
        pass

    def on_act_start(self, ctx: UnifiedContext) -> None:
        pass

    def on_act_end(self, ctx: UnifiedContext, result: Any) -> None:
        pass

    def after_tool_executed(self, ctx: UnifiedContext, tool_call: Any, result: Any) -> None:
        pass

    def on_tool_error(self, ctx: UnifiedContext, tool_call: Any, error: BaseException) -> None:
        pass

    def on_confirmation(self, ctx: UnifiedContext, tool_call: Any) -> None:
        pass

    def on_observe(self, ctx: UnifiedContext) -> None:
        pass

    def on_error(self, ctx: UnifiedContext, error: BaseException) -> None:
        pass

    def finalize(self, ctx: UnifiedContext, content: str | None) -> str | None:
        return content

    def after_iteration(self, ctx: UnifiedContext) -> None:
        pass


__all__ = ["UnifiedContext", "UnifiedHook"]