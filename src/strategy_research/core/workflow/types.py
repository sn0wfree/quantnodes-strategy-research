from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
