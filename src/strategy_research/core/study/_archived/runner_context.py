"""RunnerContext — dependency injection container for extracted modules.

Decouples utility modules from the AutoresearchRunner instance by
providing a lightweight dataclass that carries shared state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RunnerContext:
    """Shared context for all extracted engine/utility modules.

    Created by ``AutoresearchRunner._to_context()`` and passed to
    extracted functions instead of the full runner instance.
    """
    study_id: str
    session: str
    study: Any  # StudyRecord
    study_store: Any  # StudyStore
    control: Any  # ControlToken
    emit_fn: Callable[[str, str, dict], None]  # session_id, event, data
    goal_store: Any = None
    # AEGIS state (mutable — shared with runner)
    prev_passed: set[str] = field(default_factory=set)
    best_score: float = 0.0
    idle_rounds: int = 0
    # Budget state (mutable — shared with runner)
    total_used_time: float = 0.0
    total_used_turns: int = 0
    # Trace
    trace_id: str = ""
    # Plugin registry hook (for test injection)
    plugin_registry: Any = None
    # Loop strategy
    loop_strategy: Any = None
