"""Finance research goal subsystem (P3-a).

This package implements a research-only goal ledger for tracking finance
research objectives, claims, criteria, and evidence. It does NOT support
live trading or order execution (see policy.py).
"""

from __future__ import annotations

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

__all__ = [
    "AuditRow",
    "EvidenceInput",
    "EvidenceRecord",
    "GoalClaim",
    "GoalCriterion",
    "GoalRecord",
    "GoalStatus",
    "GoalStore",
    "RiskTier",
    "StaleGoalError",
    "normalize_required_text",
    "reject_live_execution_objective",
]