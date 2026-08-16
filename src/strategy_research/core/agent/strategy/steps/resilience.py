"""Default ResilienceStep — no-op stub (v0.1).

The circuit breaker stays in AgentLoop._circuit_breaker for v0.1; this
Step is a hook for future strategies.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultResilienceStep:
    @property
    def name(self) -> str:
        return "resilience"

    def is_open(self, ctx: LoopContext) -> bool:
        return False

    def record_success(self, ctx: LoopContext, tool_name: str) -> None:
        return None

    def record_failure(self, ctx: LoopContext, tool_name: str) -> None:
        return None


__all__ = ["DefaultResilienceStep"]
