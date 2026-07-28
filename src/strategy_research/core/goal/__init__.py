"""Finance research goal subsystem (P3-a).

This package implements a research-only goal ledger for tracking finance
research objectives, claims, criteria, and evidence. It does NOT support
live trading or order execution (see policy.py).
"""

from __future__ import annotations

from .context import (
    CONTINUABLE_GOAL_STATUSES,
    OPEN_CRITERION_STATUSES,
    criterion_is_covered,
    default_goal_criteria,
    format_goal_context,
    format_goal_continuation_prompt,
    get_current_goal_context,
    goal_needs_continuation,
    goal_progress_tuple,
)
from .models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    RiskTier,
    StaleGoalError,
)
from .policy import normalize_required_text, reject_live_execution_objective
from .store import GoalStore
from .workflow import (
    GoalWorkflowConfig,
    GoalWorkflowRunner,
    GoalWorkflowState,
)
from .workflow_config import load_goal_workflow, list_goal_workflows

__all__ = [
    "AuditRow",
    "CONTINUABLE_GOAL_STATUSES",
    "EvidenceInput",
    "EvidenceRecord",
    "GoalClaim",
    "GoalCriterion",
    "GoalRecord",
    "GoalStatus",
    "GoalStore",
    "GoalWorkflowConfig",
    "GoalWorkflowRunner",
    "GoalWorkflowState",
    "OPEN_CRITERION_STATUSES",
    "RiskTier",
    "StaleGoalError",
    "criterion_is_covered",
    "default_goal_criteria",
    "format_goal_context",
    "format_goal_continuation_prompt",
    "get_current_goal_context",
    "goal_needs_continuation",
    "goal_progress_tuple",
    "load_goal_workflow",
    "list_goal_workflows",
    "normalize_required_text",
    "reject_live_execution_objective",
]
