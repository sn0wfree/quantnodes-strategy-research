"""Default ToolExecutionStep — no-op stub (v0.1).

The tool dispatch + retry logic stays in AgentLoop._execute_tool_batch
for v0.1; this Step is a hook for future strategies (e.g. serial-only
MinimalStrategy).
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultToolExecutionStep:
    @property
    def name(self) -> str:
        return "tool_execution"

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        return ctx


__all__ = ["DefaultToolExecutionStep"]
