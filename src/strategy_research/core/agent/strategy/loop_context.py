"""LoopContext — shared state passed between LoopStrategy steps.

P1-1 introduces a composable ``LoopStrategy`` whose ``Step``
implementations exchange state through this context. Defaults are
chosen so v0.1 callers can build a fresh context in one line.

Why a dedicated dataclass instead of reusing ``messages`` + ``result``
directly: LoopStrategy may add per-step fields (recent_hashes,
previous_summary, …) without touching the legacy ``AgentLoop`` state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopContext:
    """Mutable container the steps read/write during a loop run.

    Built fresh per ``AgentLoop.run()`` and threaded through every
    ``Step.execute``. Steps mutate fields rather than returning new
    contexts to keep call sites short.
    """

    task: str
    context: str | None = None
    history: list[dict[str, Any]] | None = None

    messages: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    t0: float = 0.0

    # Last LLM response (LLMCallStep writes it; StopStep/ContinuationStep
    # read it to decide whether to keep iterating).
    response: Any | None = None
    response_was_tool_call: bool = False
    response_content: str = ""

    # Tool execution state.
    recent_hashes: list[str] = field(default_factory=list)
    tool_calls_made: int = 0

    # Compaction state (CompactionStep writes; later steps read).
    previous_summary: str | None = None
    last_seq: int = 0

    # Stop signals — a step may set should_stop + stop_reason to break
    # the loop early. ``should_continue`` reads these to decide.
    should_stop: bool = False
    stop_reason: str | None = None

    # Free-form bag for step-specific payloads (no per-step field
    # explosion; v0.1 keeps it minimal).
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["LoopContext"]
