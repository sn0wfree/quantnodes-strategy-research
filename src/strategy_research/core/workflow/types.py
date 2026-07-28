from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class AgentCall:
    agent_name: str
    prompt: str
    context: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RoundResult:
    round_num: int
    agent_results: list[AgentStatus] = field(default_factory=list)
    keep: bool = False
    calmar: float = 0.0
    sharpe: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SwarmTask:
    strategy_id: str
    workspace: str
    rounds: list[RoundResult] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


# ── SwarmHook Protocol (P3.1) ────────────────────────────────


class SwarmHook(Protocol):
    """Hook point during SwarmRuntime.execute().

    Implement any subset of callbacks.  SwarmRuntime calls them at
    the corresponding execution points.  Callbacks must not raise;
    exceptions are logged and swallowed.
    """

    @property
    def name(self) -> str:
        """Human-readable hook name for logging."""
        ...

    def on_layer_start(
        self, layer_idx: int, agents: list[str], context: dict[str, Any],
    ) -> None:
        """Called before a new DAG layer begins execution."""
        ...

    def on_layer_complete(
        self,
        layer_idx: int,
        agents: list[str],
        results: dict[str, Any],
    ) -> None:
        """Called after all agents in a layer have finished."""
        ...

    def on_agent_complete(
        self,
        agent_id: str,
        result: Any,
        context: dict[str, Any],
    ) -> None:
        """Called after a single agent finishes (success or error)."""
        ...

    def should_stop(self) -> bool:
        """Return True to terminate the DAG early (e.g. goal complete)."""
        return False
