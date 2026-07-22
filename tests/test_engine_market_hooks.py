"""Tests for engine/market_hooks.py — crypto funding fee + liquidation tier table."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.core.engine.market_hooks import (
    FUNDING_HOURS,
    _maintenance_rate,
    calc_crypto_funding_fee,
    check_crypto_liquidation,
)
from strategy_research.core.engine.models import Position


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def long_position():
    return Position(
        symbol="BTC/USDT",
        direction=1,
        entry_price=100.0,
        entry_time=pd.Timestamp("2024-01-01 00:00:00"),
        size=1.0,
        leverage=10.0,
    )


@pytest.fixture
def short_position():
    return Position(
        symbol="ETH/USDT",
        direction=-1,
        entry_price=2000.0,
        entry_time=pd.Timestamp("2024-01-01 00:00:00"),
        size=1.0,
        leverage=5.0,
    )


@pytest.fixture
def bar_up():
    return pd.Series({"close": 110.0, "open": 109.0, "high": 111.0, "low": 108.0, "volume": 100.0})


@pytest.fixture
def bar_down():
    return pd.Series({"close": 90.0, "open": 91.0, "high": 92.0, "low": 89.0, "volume": 100.0})


# ============================================================
# Funding Hours
# ============================================================


class TestFundingHours:
    def test_three_funding_hours(self):
        assert FUNDING_HOURS == {0, 8, 16}

    def test_funding_at_midnight(self):
        ts = pd.Timestamp("2024-01-01 00:00:00")
        assert ts.hour in FUNDING_HOURS

    def test_funding_at_8am(self):
        ts = pd.Timestamp("2024-01-01 08:00:00")
        assert ts.hour in FUNDING_HOURS

    def test_funding_at_4pm(self):
        ts = pd.Timestamp("2024-01-01 16:00:00")
        assert ts.hour in FUNDING_HOURS

    def test_no_funding_at_10am(self):
        ts = pd.Timestamp("2024-01-01 10:00:00")
        assert ts.hour not in FUNDING_HOURS


# ============================================================
# calc_crypto_funding_fee
# ============================================================


class TestCryptoFundingFee:
    def test_no_position_returns_zero(self, bar_up):
        fee = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, pd.Timestamp("2024-01-01 00:00:00"),
            {}, 0.0001, set(), set(),
        )
        assert fee == 0.0

    def test_funding_at_funding_hour_long(self, long_position, bar_up):
        ts = pd.Timestamp("2024-01-01 00:00:00")
        fee = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts,
            {"BTC/USDT": long_position}, 0.0001, set(), set(),
        )
        # fee = size * mark_price * funding_rate * direction
        # = 1.0 * 110.0 * 0.0001 * 1 = 0.011
        assert fee == pytest.approx(0.011, abs=1e-9)

    def test_funding_at_funding_hour_short(self, short_position, bar_up):
        ts = pd.Timestamp("2024-01-01 00:00:00")
        fee = calc_crypto_funding_fee(
            "ETH/USDT", bar_up, ts,
            {"ETH/USDT": short_position}, 0.0001, set(), set(),
        )
        # size * mark * rate * direction
        # 1.0 * 110.0 * 0.0001 * -1 = -0.011
        assert fee == pytest.approx(-0.011, abs=1e-9)

    def test_dedup_funding_hour(self, long_position, bar_up):
        ts = pd.Timestamp("2024-01-01 00:00:00")
        applied = set()
        fee1 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        fee2 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        assert fee1 != 0.0
        assert fee2 == 0.0  # deduped

    def test_dedup_non_funding_hour(self, long_position, bar_up):
        ts = pd.Timestamp("2024-01-01 10:00:00")  # not a funding hour
        daily_done = set()
        fee1 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts,
            {"BTC/USDT": long_position}, 0.0001, set(), daily_done,
        )
        fee2 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts,
            {"BTC/USDT": long_position}, 0.0001, set(), daily_done,
        )
        assert fee1 != 0.0
        assert fee2 == 0.0  # deduped via daily_done_set

    def test_different_funding_hours(self, long_position, bar_up):
        applied = set()
        ts0 = pd.Timestamp("2024-01-01 00:00:00")
        ts8 = pd.Timestamp("2024-01-01 08:00:00")
        fee0 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts0,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        fee8 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts8,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        assert fee0 != 0.0
        assert fee8 != 0.0
        assert fee0 == pytest.approx(fee8)

    def test_different_dates_no_dedup(self, long_position, bar_up):
        applied = set()
        ts1 = pd.Timestamp("2024-01-01 00:00:00")
        ts2 = pd.Timestamp("2024-01-02 00:00:00")
        fee1 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts1,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        fee2 = calc_crypto_funding_fee(
            "BTC/USDT", bar_up, ts2,
            {"BTC/USDT": long_position}, 0.0001, applied, set(),
        )
        assert fee1 != 0.0
        assert fee2 != 0.0

    def test_missing_close_falls_back_to_entry(self, long_position):
        bar = pd.Series({"open": 109.0})  # no 'close'
        ts = pd.Timestamp("2024-01-01 00:00:00")
        fee = calc_crypto_funding_fee(
            "BTC/USDT", bar, ts,
            {"BTC/USDT": long_position}, 0.0001, set(), set(),
        )
        # mark_price falls back to entry_price=100
        # fee = 1.0 * 100 * 0.0001 * 1 = 0.01
        assert fee == pytest.approx(0.01, abs=1e-9)


# ============================================================
# Maintenance Rate Tier Table
# ============================================================


class TestMaintenanceRate:
    def test_tier_1_under_100k(self):
        assert _maintenance_rate(50_000) == 0.004

    def test_tier_1_at_100k(self):
        assert _maintenance_rate(100_000) == 0.004

    def test_tier_2_under_500k(self):
        assert _maintenance_rate(300_000) == 0.006

    def test_tier_2_at_500k(self):
        assert _maintenance_rate(500_000) == 0.006

    def test_tier_3_under_1m(self):
        assert _maintenance_rate(800_000) == 0.01

    def test_tier_3_at_1m(self):
        assert _maintenance_rate(1_000_000) == 0.01

    def test_tier_4_under_5m(self):
        assert _maintenance_rate(3_000_000) == 0.02

    def test_tier_4_at_5m(self):
        assert _maintenance_rate(5_000_000) == 0.02

    def test_tier_5_under_10m(self):
        assert _maintenance_rate(8_000_000) == 0.05

    def test_tier_5_at_10m(self):
        assert _maintenance_rate(10_000_000) == 0.05

    def test_tier_6_over_10m(self):
        assert _maintenance_rate(50_000_000) == 0.10

    def test_tier_6_very_large(self):
        assert _maintenance_rate(1_000_000_000) == 0.10


# ============================================================
# check_crypto_liquidation
# ============================================================


class TestCryptoLiquidation:
    def test_no_position_no_liquidation(self, bar_up):
        result = check_crypto_liquidation(
            "BTC/USDT", bar_up, {}
        )
        assert result is False

    def test_leverage_1_no_liquidation(self, long_position, bar_down):
        # Position is frozen — create a new one with leverage=1.0
        pos_no_lev = Position(
            symbol=long_position.symbol,
            direction=long_position.direction,
            entry_price=long_position.entry_price,
            entry_time=long_position.entry_time,
            size=long_position.size,
            leverage=1.0,
        )
        result = check_crypto_liquidation(
            "BTC/USDT", bar_down, {"BTC/USDT": pos_no_lev}
        )
        assert result is False  # leverage <= 1.0 short-circuits

    def test_long_profitable_no_liquidation(self, bar_up):
        pos = Position(
            symbol="BTC/USDT", direction=1,
            entry_price=100.0, entry_time=pd.Timestamp("2024-01-01"),
            size=1.0, leverage=10.0,
        )
        result = check_crypto_liquidation(
            "BTC/USDT", bar_up, {"BTC/USDT": pos}
        )
        assert result is False  # profitable long

    def test_long_big_loss_liquidated(self):
        # entry=100, mark=85, size=1, leverage=10
        # margin = 100/10 = 10
        # unrealized = (85-100) = -15
        # margin + unrealized = -5
        # notional = 85, maint_rate(tier 1) = 0.004
        # maint_margin = 85 * 0.004 = 0.34
        # -5 <= 0.34 → liquidated
        pos = Position(
            symbol="BTC/USDT", direction=1,
            entry_price=100.0, entry_time=pd.Timestamp("2024-01-01"),
            size=1.0, leverage=10.0,
        )
        bar = pd.Series({"close": 85.0})
        result = check_crypto_liquidation(
            "BTC/USDT", bar, {"BTC/USDT": pos}
        )
        assert result is True

    def test_short_big_loss_liquidated(self):
        # entry=2000, mark=2200, direction=-1, size=1, leverage=5
        # margin = 2000/5 = 400
        # unrealized = -1 * 1 * (2200-2000) = -200
        # margin + unrealized = 200
        # notional = 2200, tier 1: 0.004 → maint_margin = 8.8
        # 200 <= 8.8 → not liquidated (still has margin)
        pos = Position(
            symbol="ETH/USDT", direction=-1,
            entry_price=2000.0, entry_time=pd.Timestamp("2024-01-01"),
            size=1.0, leverage=5.0,
        )
        bar = pd.Series({"close": 2200.0})
        result = check_crypto_liquidation(
            "ETH/USDT", bar, {"ETH/USDT": pos}
        )
        # 200 not <= 8.8 → no liquidation
        assert result is False

    def test_missing_close_falls_back(self):
        pos = Position(
            symbol="BTC/USDT", direction=1,
            entry_price=100.0, entry_time=pd.Timestamp("2024-01-01"),
            size=1.0, leverage=10.0,
        )
        bar = pd.Series({"open": 109.0})  # no 'close'
        result = check_crypto_liquidation(
            "BTC/USDT", bar, {"BTC/USDT": pos}
        )
        # mark = entry_price = 100, no loss, no liquidation
        assert result is False

    def test_tier_5_liquidation_threshold(self):
        # notional > 10M → tier 6: 10% maint
        pos = Position(
            symbol="BTC/USDT", direction=1,
            entry_price=10_000_000.0, entry_time=pd.Timestamp("2024-01-01"),
            size=1.0, leverage=2.0,
        )
        # margin = 10M/2 = 5M
        # notional at mark = 10M (entry) → tier 5: 0.05
        # maint_margin = 10M * 0.05 = 500K
        # 5M + 0 (no loss) > 500K → no liquidation
        bar = pd.Series({"close": 10_000_000.0})
        result = check_crypto_liquidation(
            "BTC/USDT", bar, {"BTC/USDT": pos}
        )
        assert result is False