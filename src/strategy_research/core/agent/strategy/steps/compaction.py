"""Default CompactionStep — no-op stub (v0.1)."""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultCompactionStep:
    @property
    def name(self) -> str:
        return "compaction"

    def should_run(self, ctx: LoopContext) -> bool:
        return False

    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        return ctx


__all__ = ["DefaultCompactionStep"]
