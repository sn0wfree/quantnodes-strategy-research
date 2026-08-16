"""Default PreRunStep — placeholder (v0.1).

v0.1 keeps PreRunStep as a no-op stub. The full hypothesis / goal /
context-injection logic stays inside AgentLoop._prepare_run for now;
the migration to a Step happens in L7 once AgentLoop._run_loop_core
is rewritten to drive the strategy. See ``loop_strategy.py`` for
composition.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultPreRunStep:
    """No-op PreRunStep; the real pre-run still lives in AgentLoop."""

    @property
    def name(self) -> str:
        return "pre_run"

    def should_run(self, ctx: LoopContext) -> bool:
        return True

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        # v0.1: AgentLoop builds the initial messages + result via
        # ``_prepare_run`` and stores them in ``ctx.messages`` before
        # the loop starts. The Step itself is a hook for future
        # strategy variants that want custom pre-run injection.
        return ctx


__all__ = ["DefaultPreRunStep"]
