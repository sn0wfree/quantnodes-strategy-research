"""Market data fixtures — OHLCV panels, prices, returns."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def make_random_prices(
    n_days: int = 252,
    n_assets: int = 5,
    *,
    start: float = 100.0,
    drift: float = 0.0005,
    vol: float = 0.02,
    seed: int = 42,
    freq: Literal["B", "D"] = "B",
    start_date: str = "2024-01-01",
) -> pd.DataFrame:
    """Generate a wide random-walk price panel.

    Args:
        n_days: Number of trading days.
        n_assets: Number of assets (columns).
        start: Starting price level.
        drift: Daily mean return.
        vol: Daily volatility (std of returns).
        seed: RNG seed for reproducibility.
        freq: "B" (business days) or "D" (calendar days).
        start_date: First date.

    Returns:
        DataFrame of shape ``(n_days, n_assets)`` indexed by a DatetimeIndex.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start_date, periods=n_days) if freq == "B" \
        else pd.date_range(start_date, periods=n_days, freq=freq)
    returns = rng.normal(drift, vol, size=(n_days, n_assets))
    prices = start * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(prices, index=dates, columns=[f"S{i}" for i in range(n_assets)])


def make_random_returns(
    n_days: int = 252,
    n_assets: int = 5,
    *,
    mean: float = 0.0,
    std: float = 0.01,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a wide random returns panel (no index)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    return pd.DataFrame(
        rng.normal(mean, std, size=(n_days, n_assets)),
        index=dates,
        columns=[f"S{i}" for i in range(n_assets)],
    )


def make_ohlcv_panel(
    n_days: int = 252,
    n_assets: int = 5,
    *,
    seed: int = 42,
    include_derived: bool = True,
) -> dict[str, pd.DataFrame]:
    """Generate a complete OHLCV+ panel dictionary.

    Includes ``open / high / low / close / volume / amount / vwap / returns``.
    When ``include_derived=True`` also adds ``adv5/10/20/30/60`` rolling volume
    means (used by some alpha101 / gtja191 / qlib158 formulas).

    Returns:
        dict of column-name → DataFrame. All frames share the same
        DatetimeIndex and column labels.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    cols = [f"S{i}" for i in range(n_assets)]

    close = make_random_prices(n_days=n_days, n_assets=n_assets, seed=seed)
    # open_ = previous close + small jitter (avoids shift==0 artefacts)
    open_ = close.shift(1).fillna(close.iloc[0]) * (
        1 + rng.normal(0, 0.002, close.shape)
    )
    high = close * (1 + np.abs(rng.normal(0, 0.005, close.shape)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, close.shape)))
    volume = pd.DataFrame(
        rng.uniform(1e6, 1e8, close.shape), index=dates, columns=cols
    )
    amount = pd.DataFrame(
        rng.uniform(1e7, 1e9, close.shape), index=dates, columns=cols
    )

    panel: dict[str, pd.DataFrame] = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "vwap": close * (1 + rng.normal(0, 0.001, close.shape)),
        "returns": close.pct_change().fillna(0),
    }

    if include_derived:
        for w in (5, 10, 15, 20, 30, 50, 60):
            panel[f"adv{w}"] = volume.rolling(w).mean().fillna(volume.mean())

    return panel


def make_panel(
    n_days: int = 252,
    n_assets: int = 5,
    *,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """Alias for ``make_ohlcv_panel(include_derived=True)``."""
    return make_ohlcv_panel(n_days=n_days, n_assets=n_assets, seed=seed)