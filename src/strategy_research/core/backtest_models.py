"""Unified ``BacktestResult`` (P0-2 Phase F).

The codebase had two parallel ``BacktestResult`` dataclasses:
- ``core.utils.backtest_engine.BacktestResult`` (4 fields)
- ``core.utils.strategy_engine.BacktestResult`` (5 fields, has factor_failures)

P0-2 Phase F consolidates them into one canonical dataclass with
``factor_failures`` defaulting to an empty list — every consumer stays
working (the legacy classes become type aliases), new code uses the
canonical one.

The dataclass lives in ``core/backtest_models.py`` (top-level) so both
engine modules can import it without crossing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class BacktestResult:
    """Canonical backtest result (P0-2 Phase F).

    All engines — callback-based, strategy-based, bar-by-bar — produce
    this shape. ``factor_failures`` is populated by ``run_from_yaml``
    after the run ends; it carries ``{factor, asset, error, occurrences}``
    entries for downstream dashboards and gates.
    """
    nav_daily: pd.Series
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]] = field(
        default_factory=list,
    )
    rebalance_dates: list[pd.Timestamp] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    factor_failures: list[dict] = field(default_factory=list)


__all__ = ["BacktestResult"]
