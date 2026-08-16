"""Default CompactionStep (L7 v0.5).

Drives context compression at the start of each iteration. Wraps the
existing ``_maybe_compact`` / ``_amaybe_compact`` methods (which
themselves delegate to the compaction engine in ``compact.py``).

After compaction, also emits ``_emit_compaction`` / ``_emit_iter_start``
/ ``_inject_todos_snapshot`` — these are the remaining per-iteration
observability calls that were previously hard-coded in the skeleton.
"""

from __future__ import annotations

from ..loop_context import LoopContext
from ..protocol import CompactionStep


class DefaultCompactionStep:
    """Context compression + per-iteration observability."""

    def __init__(self) -> None:
        self._loop: object | None = None

    def bind_agent_loop(self, agent_loop: object) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "compaction"

    def should_run(self, ctx: LoopContext) -> bool:
        # Always run (compaction checks threshold internally).
        return True

    async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        loop = self._loop
        if loop is None:
            return ctx

        # Delegate to the compaction engine (sync / async twin).
        if async_mode:
            messages, applied = await loop._amaybe_compact(ctx.messages)
        else:
            messages, applied = loop._maybe_compact(ctx.messages)

        if applied:
            loop._emit_compaction(applied, ctx.iteration, ctx.result)
        loop._emit_iter_start(ctx.iteration, messages)
        loop._inject_todos_snapshot(messages)

        ctx.messages = messages
        ctx.metadata["compaction_applied"] = applied
        return ctx


__all__ = ["DefaultCompactionStep"]