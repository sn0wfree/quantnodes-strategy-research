"""Default LLMCallStep — placeholder (v0.1).

v0.1 keeps the LLM call inside ``AgentLoop._get_response``; this step
is a stub that returns the context unchanged. The real migration is
in L7 once ``_run_loop_core`` is rewritten to drive the strategy.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultLLMCallStep:
    """No-op LLMCallStep; AgentLoop still calls the LLM directly."""

    @property
    def name(self) -> str:
        return "llm_call"

    def should_run(self, ctx: LoopContext) -> bool:
        return ctx.response is None

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        # v0.1: AgentLoop populates ``ctx.response`` itself before
        # consulting the strategy's StopStep / ContinuationStep.
        # Real implementation lands in L7.
        return ctx


__all__ = ["DefaultLLMCallStep"]
