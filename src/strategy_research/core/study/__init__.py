"""Study task system — autoresearch 模式的服务化核心任务.

A study pairs a goal ledger (objective / criteria / evidence, owned by
``GoalStore``) with the execution state for an autoresearch loop driving
a ``workspace_path`` + ``strategy_name``. See ``docs/study-longhorizon-plan.md``.

Public API:
    StudyStore        — persistence (``studies`` table, shares goals.db)
    StudyRecord       — frozen dataclass for a study execution row
    StudyStatus       — lifecycle enum (queued / running / paused / ...)
    MetricTarget      — quantitative acceptance target (name/op/value)
    default_metric_targets — mirror ``AcceptanceConfig`` defaults
    ACTIVE_EXECUTION_STATUSES — statuses that count as "in flight"
"""

from __future__ import annotations

from .models import (
    ACTIVE_EXECUTION_STATUSES,
    MetricTarget,
    StudyRecord,
    StudyStatus,
    default_metric_targets,
)
from .store import StudyStore
from .executor import (
    AutoresearchExecutor,
    ControlToken,
    EventEmitter,
    NullEmitter,
    ShutdownReason,
    acceptance_config_from_targets,
    meets_metric_targets,
)
from .scheduler import StudyScheduler, make_event_bus_emitter

__all__ = [
    "ACTIVE_EXECUTION_STATUSES",
    "AutoresearchExecutor",
    "ControlToken",
    "EmitterProtocol",
    "EventEmitter",
    "MetricTarget",
    "NullEmitter",
    "ShutdownReason",
    "StudyRecord",
    "StudyScheduler",
    "StudyStatus",
    "StudyStore",
    "acceptance_config_from_targets",
    "default_metric_targets",
    "make_event_bus_emitter",
    "meets_metric_targets",
]