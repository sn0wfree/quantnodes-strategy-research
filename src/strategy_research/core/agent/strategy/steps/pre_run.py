"""Default PreRunStep (L7).

Bound to an AgentLoop via ``bind_agent_loop`` — ``execute`` delegates to
``agent_loop._prepare_run`` so the strategy step has the same effect
as the legacy hard-coded pre-run block in ``_run_loop_core``.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultPreRunStep:
    """PreRunStep that delegates to ``agent_loop._prepare_run``."""

    def __init__(self) -> None:
        self._loop: object | None = None

    def bind_agent_loop(self, agent_loop: object) -> None:
        """L7 wiring — AgentLoop injects itself after strategy creation."""
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "pre_run"

    def should_run(self, ctx: LoopContext) -> bool:
        return True

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        if self._loop is None:
            return ctx
        full_task, result, messages, t0 = self._loop._prepare_run(
            ctx.task, ctx.context, ctx.history,
        )
        ctx.messages = list(messages)
        ctx.result = result
        ctx.iteration = 0
        ctx.t0 = t0
        ctx.metadata["full_task"] = full_task
        return ctx


__all__ = ["DefaultPreRunStep"]
