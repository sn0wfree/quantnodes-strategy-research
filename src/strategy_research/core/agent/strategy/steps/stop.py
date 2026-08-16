"""Default StopStep — placeholder (v0.1).

The real stop semantics (response.is_tool_calls check + max-iterations)
stay inside AgentLoop._run_loop_core for v0.1. The Step is a stub
that defers to ``ctx.should_stop`` so future strategies can wire their
own stop logic without touching AgentLoop.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultStopStep:
    @property
    def name(self) -> str:
        return "stop"

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        if ctx.should_stop:
            return True, ctx.stop_reason
        # v0.1 fallback: respect ctx.iteration ≥ some max via the
        # strategy-level config; AgentLoop owns that loop.
        return False, None


__all__ = ["DefaultStopStep"]
