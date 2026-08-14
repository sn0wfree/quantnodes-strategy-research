"""Data models for the study task system.

A study is a long-horizon, goal-driven automation of the autoresearch
loop. The ledger layer (GoalStore) tracks the research objective,
acceptance criteria and evidence; this module tracks the *execution*
state (queued / running / paused / error ...) plus the binding to a
workspace + strategy that the autoresearch loop drives.

See ``docs/study-longhorizon-plan.md`` for the design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StudyStatus(str, Enum):
    """Lifecycle states for a study's execution layer.

    Distinct from ``GoalStatus`` (the ledger lifecycle): a study can be
    ``running`` while its goal is still ``active``, or ``complete``
    while its goal turns ``complete`` alongside it.
    """

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"  # Server restart killed running study; manual resume required
    ERROR = "error"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    BUDGET_LIMITED = "budget_limited"
    MONITORING = "monitoring"   # Phase 3: post-completion periodic checks
    NEEDS_REFRESH = "needs_refresh"  # Phase 3: monitor drift detected
    EARLY_STOPPED = "early_stopped"  # AEGIS: 3+ idle rounds without improvement


# Execution statuses that count as "active" — a session may have at
# most one study in any of these states (the scheduler enforces this).
# NOTE: MONITORING is intentionally excluded — it's a passive background
# check that doesn't occupy the session's processing slot.
# INTERRUPTED is NOT active — it waits for manual user resume.
ACTIVE_EXECUTION_STATUSES = frozenset(
    {
        StudyStatus.QUEUED,
        StudyStatus.RUNNING,
        StudyStatus.PAUSED,
    }
)


@dataclass(frozen=True)
class MetricTarget:
    """A single quantitative acceptance target for a study.

    Examples:
        ``{"name": "calmar", "op": ">=", "value": 0.5}``
        ``{"name": "max_dd", "op": "<=", "value": -0.15}``
    """

    name: str
    op: str  # one of >=, <=, >, <, ==
    value: float

    def as_dict(self) -> dict:
        return {"name": self.name, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class StudyRecord:
    """Persisted study execution record.

    Pairs a ``goal_id`` (the ledger account tracking criteria / evidence /
    progress) with the execution state for the autoresearch loop driving
    a ``workspace_path`` + ``strategy_name``.
    """

    study_id: str
    session_id: str  # v2 单身份：= study_id（执行身份 + 事件频道 + goal 隔离域）
    goal_id: str | None
    objective: str
    executor_type: str  # 'autoresearch' | 'workflow'
    workspace_path: str
    strategy_name: str
    # 创建者 chat 会话 — 归属查询（get_active_study/list_studies）与 IDOR
    # 校验（_verify_study_ownership）用途；旧数据回填=session_id。
    owner_session_id: str | None = None
    metric_targets: list[dict] = field(default_factory=list)
    budget_token: int | None = None
    budget_turn: int | None = None
    budget_time_seconds: int | None = None
    cooldown_base: float = 30.0
    cooldown_jitter: float = 10.0
    min_cooldown: float = 1.0
    max_rounds: int | None = None
    lazy_detection_interval: int = 10
    keep_recent: int = 10
    behavior: str | None = None  # None = real LLM; 'static'/'varying'/'improving' = stub
    execution_status: StudyStatus = StudyStatus.QUEUED
    current_round: int = 0
    last_metrics: dict | None = None
    last_verdict: str | None = None
    last_error: str | None = None
    last_traceback: str | None = None
    heartbeat: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    # ── Phase 3: post-completion monitoring ────────────────────────
    monitor_interval_seconds: int | None = None  # None = no monitoring
    last_monitor_check_at: str | None = None
    monitor_drift_count: int = 0


@dataclass(frozen=True)
class StudyRoundRecord:
    """A single round record in a study's execution history.

    Persisted in the ``study_rounds`` table. Tracks per-round metrics,
    verdict, evidence, and configuration changes for AEGIS attribution.
    """

    round_id: str
    study_id: str
    goal_id: str | None
    session_id: str
    round_num: int
    run_name: str
    metrics: dict = field(default_factory=dict)
    verdict: str = "discard"
    evidence_ids: list[str] = field(default_factory=list)
    config_changes: dict | None = None
    agent_output: str | None = None
    review: dict | None = None   # v2 phase-2 overlay (manifest review section)
    error: str | None = None
    factor_failures: list[dict] = field(default_factory=list)
    verdict_reason: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class StudyDirective:
    """User-issued research direction injected into the next round.

    Phase 2 (mid-execution interaction): a directive is appended to
    ``run_research_round``'s prompt context for the researcher agent at
    the start of the next round. Once consumed, ``consumed_at`` is set
    so the directive is not re-applied on later rounds.
    """

    directive_id: str
    study_id: str
    content: str
    issued_by: str | None
    created_at: str
    consumed_at: str | None = None


def default_metric_targets() -> list[dict]:
    """Return the default acceptance targets for a study.

    These mirror ``AcceptanceConfig`` defaults
    (``hard_calmar_min=0.5 / sharpe_min=0.3 / max_dd_min=-0.15``) so a
    study created without explicit targets still has a measurable
    acceptance bar.
    """

    return [
        {"name": "calmar", "op": ">=", "value": 0.5},
        {"name": "sharpe", "op": ">=", "value": 0.3},
        {"name": "max_dd", "op": ">=", "value": -0.15},
    ]
