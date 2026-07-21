"""Backtest 数据模型 — 借鉴自 vibe-trading backtest/models.py.

3 个 immutable dataclass：Position / TradeRecord / EquitySnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Position:
    """未平仓头寸（多/空）.

    Attributes:
        symbol: 标的代码.
        direction: 1=多, -1=空.
        entry_price: 开仓价.
        entry_time: 开仓时间.
        size: 持仓数量 (股 / 币数).
        leverage: 杠杆倍数 (1=现货/股票默认).
        entry_bar_idx: 开仓时对应 dates 数组的索引, 用于 holding_bars 计算.
        entry_commission: 开仓时手续费.
    """

    symbol: str
    direction: int
    entry_price: float
    entry_time: pd.Timestamp
    size: float
    leverage: float = 1.0
    entry_bar_idx: int = 0
    entry_commission: float = 0.0


@dataclass(frozen=True)
class TradeRecord:
    """已完成 round-trip 交易记录.

    Attributes:
        symbol: 标的代码.
        direction: 1=多, -1=空.
        entry_price: 开仓价.
        exit_price: 平仓价.
        entry_time: 开仓时间.
        exit_time: 平仓时间.
        size: 数量.
        leverage: 杠杆.
        pnl: 已实现盈亏 (cash).
        pnl_pct: 盈亏占保证金比例 (%).
        exit_reason: "signal" / "liquidation" / "end_of_backtest" / 其他.
        holding_bars: 持仓 bar 数.
        commission: 总手续费 (开 + 平).
    """

    symbol: str
    direction: int
    entry_price: float
    exit_price: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    size: float
    leverage: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    holding_bars: int
    commission: float


@dataclass(frozen=True)
class EquitySnapshot:
    """某 bar 的组合状态快照.

    Attributes:
        timestamp: 快照时间.
        capital: 自由现金.
        unrealized: 总浮动盈亏.
        equity: 总权益 (= capital + margin + unrealized).
        positions: 持仓数.
    """

    timestamp: pd.Timestamp
    capital: float
    unrealized: float
    equity: float
    positions: int = 0
