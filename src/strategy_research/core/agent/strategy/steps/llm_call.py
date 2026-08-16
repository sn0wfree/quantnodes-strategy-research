"""Default LLMCallStep (L7).

Bound to an AgentLoop via ``bind_agent_loop``. ``execute`` delegates to
``agent_loop._get_response``; failure / ``None`` sets ``ctx.should_stop``
so the step chain exits.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultLLMCallStep:
    """LLMCallStep that delegates to ``agent_loop._get_response``."""

    def __init__(self) -> None:
        self._loop: object | None = None

    def bind_agent_loop(self, agent_loop: object) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "llm_call"

    def should_run(self, ctx: LoopContext) -> bool:
        return True

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        if self._loop is None:
            return ctx
        # Build a per-iteration hook_ctx from the loop's hook context
        # helper. ``_get_response`` expects a LoopResult to fill in.
        hook_ctx = self._loop._build_hook_context(ctx.iteration, ctx.messages)
        try:
            response = self._loop._get_response(
                ctx.messages, ctx.iteration, async_mode, hook_ctx, ctx.result,
            )
        except Exception as exc:
            # Preserve the legacy error-handling path: record error on
            # the result, set should_stop so the step chain breaks.
            ctx.metadata.setdefault("errors", []).append(str(exc))
            ctx.should_stop = True
            ctx.stop_reason = "error"
            return ctx
        if response is None:
            ctx.should_stop = True
            ctx.stop_reason = "llm_none"
            return ctx
        ctx.response = response
        ctx.response_was_tool_call = bool(getattr(response, "tool_calls", None))
        ctx.response_content = getattr(response, "content", "") or ""
        # Mirror _append_assistant_msg so messages carry the response.
        self._loop._append_assistant_msg(
            response, ctx.messages, ctx.result, ctx.iteration,
        )
        return ctx


__all__ = ["DefaultLLMCallStep"]
