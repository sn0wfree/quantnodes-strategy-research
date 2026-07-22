"""Market type registry for validation (P3-c).

This module defines the multi-market interface that validation tools can
target. Per the P3-c user decision, ONLY ``MarketType.A_SHARE`` is fully
implemented in v0.3.0. The other markets are reserved as a forward
contract — the runner accepts them but emits a ``UserWarning`` and falls
back to A-share defaults so that downstream callers cannot silently
misinterpret results.

Future markets require per-market algorithm adaptations; see
``docs/validation-design.md`` for the roadmap.
"""

from __future__ import annotations

import warnings
from enum import Enum
from typing import Any


class MarketType(str, Enum):
    """Multi-market enum for validation tools."""

    A_SHARE = "a_share"
    HK_EQUITY = "hk_equity"
    US_EQUITY = "us_equity"
    CRYPTO = "crypto"
    FUTURES_CN = "futures_cn"
    FUTURES_GLOBAL = "futures_global"
    FOREX = "forex"


_MARKET_BARS_PER_YEAR: dict[MarketType, int] = {
    MarketType.A_SHARE: 252,
    MarketType.HK_EQUITY: 247,
    MarketType.US_EQUITY: 252,
    MarketType.CRYPTO: 365,
    MarketType.FUTURES_CN: 252,
    MarketType.FUTURES_GLOBAL: 252,
    MarketType.FOREX: 260,
}

_UNSUPPORTED_MARKETS: frozenset[MarketType] = frozenset({
    MarketType.CRYPTO,
    MarketType.FUTURES_CN,
    MarketType.FUTURES_GLOBAL,
    MarketType.FOREX,
})

SUPPORTED_MARKETS: frozenset[MarketType] = frozenset({
    MarketType.A_SHARE,
    MarketType.HK_EQUITY,
    MarketType.US_EQUITY,
})


def bars_per_year(market: MarketType) -> int:
    """Return the default bars_per_year for a market.

    Falls back to A_SHARE for unsupported markets (with a warning issued
    via :func:`warn_if_unsupported_market`).
    """
    return _MARKET_BARS_PER_YEAR.get(market, _MARKET_BARS_PER_YEAR[MarketType.A_SHARE])


def warn_if_unsupported_market(market: MarketType) -> None:
    """Emit a UserWarning when a market is not yet fully supported.

    Per the P3-c user decision, only A_SHARE / HK_EQUITY / US_EQUITY are
    supported in v0.3.0. Other markets still execute (with A-share
    defaults) but the caller is warned that results may be inaccurate.

    See ``docs/validation-design.md`` for the multi-market roadmap.
    """
    if market in _UNSUPPORTED_MARKETS:
        warnings.warn(
            f"MarketType.{market.value} validation is not yet implemented in v0.3.0. "
            f"Falling back to A_SHARE bars_per_year={bars_per_year(MarketType.A_SHARE)}. "
            f"Results may be inaccurate. See docs/validation-design.md for the roadmap.",
            UserWarning,
            stacklevel=2,
        )


__all__ = [
    "MarketType",
    "SUPPORTED_MARKETS",
    "bars_per_year",
    "warn_if_unsupported_market",
]