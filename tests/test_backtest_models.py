"""Tests for core/utils/backtest_models.py.

3 个 immutable dataclass 完整覆盖:
- Position / TradeRecord / EquitySnapshot
- 默认值 / frozen 不可变 / hashable / repr / asdict round-trip
"""
from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError
from datetime import datetime

import pandas as pd
import pytest

from strategy_research.core.utils.backtest_models import (
    EquitySnapshot,
    Position,
    TradeRecord,
)


# ============================================================
# Position
# ============================================================

def test_position_basic_construction():
    p = Position(
        symbol="000001.SZ",
        direction=1,
        entry_price=10.0,
        entry_time=pd.Timestamp("2024-01-01"),
        size=100,
    )
    assert p.symbol == "000001.SZ"
    assert p.direction == 1
    assert p.entry_price == 10.0
    assert p.size == 100
    assert p.leverage == 1.0  # 默认
    assert p.entry_bar_idx == 0  # 默认
    assert p.entry_commission == 0.0  # 默认


def test_position_full_construction():
    p = Position(
        symbol="BTC-USDT",
        direction=-1,
        entry_price=50000.0,
        entry_time=pd.Timestamp("2024-06-01 10:00"),
        size=0.5,
        leverage=3.0,
        entry_bar_idx=10,
        entry_commission=12.5,
    )
    assert p.direction == -1
    assert p.leverage == 3.0
    assert p.entry_bar_idx == 10
    assert p.entry_commission == 12.5


def test_position_frozen():
    p = Position(
        symbol="AAPL", direction=1, entry_price=100.0,
        entry_time=pd.Timestamp("2024-01-01"), size=10,
    )
    with pytest.raises(FrozenInstanceError):
        p.symbol = "GOOG"


def test_position_equality_and_hashable():
    """frozen=True → __hash__ 与 __eq__ 自动可用."""
    ts = pd.Timestamp("2024-01-01")
    p1 = Position(symbol="A", direction=1, entry_price=10.0, entry_time=ts, size=100)
    p2 = Position(symbol="A", direction=1, entry_price=10.0, entry_time=ts, size=100)
    p3 = Position(symbol="B", direction=1, entry_price=10.0, entry_time=ts, size=100)
    assert p1 == p2
    assert p1 != p3
    # 可哈希 → 可用作 dict key
    d = {p1: "value"}
    assert d[p2] == "value"


def test_position_repr():
    p = Position(symbol="X", direction=1, entry_price=1.0, entry_time=pd.Timestamp("2024-01-01"), size=1.0)
    r = repr(p)
    assert "Position" in r
    assert "symbol='X'" in r


def test_position_asdict_roundtrip():
    p = Position(
        symbol="Y", direction=-1, entry_price=2.5,
        entry_time=pd.Timestamp("2024-01-01"), size=200,
    )
    d = dataclasses.asdict(p)
    assert d["symbol"] == "Y"
    assert d["leverage"] == 1.0
    # 重建
    p2 = Position(**d)
    assert p == p2


# ============================================================
# TradeRecord
# ============================================================

def test_trade_record_construction():
    t = TradeRecord(
        symbol="000001.SZ",
        direction=1,
        entry_price=10.0,
        exit_price=11.0,
        entry_time=pd.Timestamp("2024-01-01"),
        exit_time=pd.Timestamp("2024-01-10"),
        size=100,
        leverage=1.0,
        pnl=100.0,
        pnl_pct=10.0,
        exit_reason="signal",
        holding_bars=9,
        commission=2.0,
    )
    assert t.pnl == 100.0
    assert t.exit_reason == "signal"
    assert t.holding_bars == 9


def test_trade_record_no_defaults():
    """TradeRecord 全部字段必填（无默认值）— 13 个必填."""
    fields = {f.name for f in dataclasses.fields(TradeRecord)}
    expected = {
        "symbol", "direction", "entry_price", "exit_price",
        "entry_time", "exit_time", "size", "leverage",
        "pnl", "pnl_pct", "exit_reason", "holding_bars", "commission",
    }
    assert fields == expected


def test_trade_record_frozen():
    t = TradeRecord(
        symbol="A", direction=1, entry_price=10.0, exit_price=11.0,
        entry_time=pd.Timestamp("2024-01-01"), exit_time=pd.Timestamp("2024-01-02"),
        size=100, leverage=1.0, pnl=100.0, pnl_pct=10.0,
        exit_reason="signal", holding_bars=1, commission=1.0,
    )
    with pytest.raises(FrozenInstanceError):
        t.pnl = 999


def test_trade_record_negative_pnl_supported():
    t = TradeRecord(
        symbol="A", direction=1, entry_price=10.0, exit_price=9.0,
        entry_time=pd.Timestamp("2024-01-01"), exit_time=pd.Timestamp("2024-01-02"),
        size=100, leverage=1.0, pnl=-100.0, pnl_pct=-10.0,
        exit_reason="stop_loss", holding_bars=1, commission=1.0,
    )
    assert t.pnl == -100.0
    assert t.exit_reason == "stop_loss"


def test_trade_record_equality():
    """same values → equal."""
    kwargs = dict(
        symbol="A", direction=1, entry_price=10.0, exit_price=11.0,
        entry_time=pd.Timestamp("2024-01-01"), exit_time=pd.Timestamp("2024-01-02"),
        size=100, leverage=1.0, pnl=100.0, pnl_pct=10.0,
        exit_reason="signal", holding_bars=1, commission=1.0,
    )
    t1 = TradeRecord(**kwargs)
    t2 = TradeRecord(**kwargs)
    assert t1 == t2


# ============================================================
# EquitySnapshot
# ============================================================

def test_equity_snapshot_basic():
    s = EquitySnapshot(
        timestamp=pd.Timestamp("2024-01-01"),
        capital=100_000.0,
        unrealized=500.0,
        equity=100_500.0,
    )
    assert s.capital == 100_000.0
    assert s.unrealized == 500.0
    assert s.equity == 100_500.0
    assert s.positions == 0  # 默认


def test_equity_snapshot_negative_unrealized():
    s = EquitySnapshot(
        timestamp=pd.Timestamp("2024-01-01"),
        capital=50_000.0,
        unrealized=-1_000.0,
        equity=49_000.0,
        positions=3,
    )
    assert s.unrealized == -1_000.0
    assert s.positions == 3


def test_equity_snapshot_frozen():
    s = EquitySnapshot(
        timestamp=pd.Timestamp("2024-01-01"),
        capital=100.0, unrealized=0.0, equity=100.0,
    )
    with pytest.raises(FrozenInstanceError):
        s.equity = 200.0


def test_equity_snapshot_repr_includes_class():
    s = EquitySnapshot(
        timestamp=pd.Timestamp("2024-01-01"),
        capital=100.0, unrealized=10.0, equity=110.0,
    )
    assert "EquitySnapshot" in repr(s)


# ============================================================
# 模块级: 跨 class 一致性
# ============================================================

def test_all_classes_are_dataclass():
    """确保 3 个都是 dataclass."""
    assert dataclasses.is_dataclass(Position)
    assert dataclasses.is_dataclass(TradeRecord)
    assert dataclasses.is_dataclass(EquitySnapshot)


def test_all_classes_are_frozen():
    """vibe-trading 借鉴: 全部 frozen=True 确保不可变."""
    for cls in (Position, TradeRecord, EquitySnapshot):
        cfg = dataclasses.fields(cls)
        # 检查 frozen=True (在 dataclass 内, frozen 标记存在 _FIELDS 元信息)
        assert getattr(cls, "__dataclass_params__", None) is not None
        assert cls.__dataclass_params__.frozen is True
