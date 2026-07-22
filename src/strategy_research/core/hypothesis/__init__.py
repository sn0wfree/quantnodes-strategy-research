"""Research hypothesis subsystem (P3-b).

File-backed registry of durable research hypotheses. Each hypothesis links
its thesis to backtest artifacts (run_cards) and tracks lifecycle status
across exploring → testing → validated / rejected / monitoring.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from .auto_create import HypothesisAutoCreator
from .registry import (
    HYPOTHESIS_STATUSES,
    Hypothesis,
    HypothesisRegistry,
    default_hypotheses_path,
)

__all__ = [
    "HYPOTHESIS_STATUSES",
    "Hypothesis",
    "HypothesisAutoCreator",
    "HypothesisRegistry",
    "default_hypotheses_path",
]