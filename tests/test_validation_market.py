"""Tests for core.validation.market — multi-market enum + warnings."""

from __future__ import annotations

import pytest

from strategy_research.core.validation.market import (
    MarketType,
    SUPPORTED_MARKETS,
    bars_per_year,
    warn_if_unsupported_market,
)


class TestMarketType:
    def test_seven_markets(self):
        assert len(MarketType) == 7

    def test_supported_markets_in_v030(self):
        """A_SHARE / HK_EQUITY / US_EQUITY only (per P3-c user decision)."""
        assert MarketType.A_SHARE in SUPPORTED_MARKETS
        assert MarketType.HK_EQUITY in SUPPORTED_MARKETS
        assert MarketType.US_EQUITY in SUPPORTED_MARKETS
        # Not yet supported in v0.3.0
        assert MarketType.CRYPTO not in SUPPORTED_MARKETS
        assert MarketType.FOREX not in SUPPORTED_MARKETS
        assert MarketType.FUTURES_CN not in SUPPORTED_MARKETS
        assert MarketType.FUTURES_GLOBAL not in SUPPORTED_MARKETS

    def test_bars_per_year_known_markets(self):
        assert bars_per_year(MarketType.A_SHARE) == 252
        assert bars_per_year(MarketType.HK_EQUITY) == 247
        assert bars_per_year(MarketType.US_EQUITY) == 252
        assert bars_per_year(MarketType.CRYPTO) == 365

    def test_unsupported_market_warning(self):
        with pytest.warns(UserWarning, match="not yet implemented"):
            warn_if_unsupported_market(MarketType.CRYPTO)

    def test_supported_market_no_warning(self, recwarn):
        warn_if_unsupported_market(MarketType.A_SHARE)
        # No warning should be emitted for supported markets
        assert len(recwarn) == 0

    def test_unsupported_warning_includes_market_name(self):
        with pytest.warns(UserWarning, match="forex"):
            warn_if_unsupported_market(MarketType.FOREX)