"""Default ProgressStep — no-op stub (v0.1).

The MD5 hash window check stays in AgentLoop._detect_no_progress for
v0.1; this Step is a hook for future strategies.
"""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultProgressStep:
    @property
    def name(self) -> str:
        return "progress"

    def record_hash(self, ctx: LoopContext, hash_value: str) -> None:
        ctx.recent_hashes.append(hash_value)
        # Keep only the last N entries (window is on LoopStrategy.config)
        if ctx.metadata.get("progress_window"):
            keep = ctx.metadata["progress_window"]
            ctx.recent_hashes = ctx.recent_hashes[-keep:]

    def is_no_progress(self, ctx: LoopContext) -> bool:
        window = ctx.metadata.get("progress_window", 3)
        if len(ctx.recent_hashes) < window:
            return False
        recent = ctx.recent_hashes[-window:]
        return len(set(recent)) == 1


__all__ = ["DefaultProgressStep"]
