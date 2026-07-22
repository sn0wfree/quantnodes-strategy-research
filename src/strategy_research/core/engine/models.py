"""Backtest engine data models — 整合自 utils/backtest_models.py

Position / TradeRecord / EquitySnapshot 三个 frozen dataclass，
bar-by-bar engine 的核心状态容器。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# Re-export from utils/backtest_models for convenience
from ..utils.backtest_models import EquitySnapshot, Position, TradeRecord

__all__ = ["Position", "TradeRecord", "EquitySnapshot"]