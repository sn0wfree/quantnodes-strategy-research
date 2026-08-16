"""Default FinalizationStep — no-op stub (v0.1).

Metrics / claim validation / git commit stay in AgentLoop._run_loop_core
for v0.1; this Step is a hook for future strategies.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultFinalizationStep:
    @property
    def name(self) -> str:
        return "finalization"

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        return ctx


__all__ = ["DefaultFinalizationStep"]
