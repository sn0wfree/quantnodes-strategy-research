"""Alpha panel fixtures."""

from __future__ import annotations

from .market import make_ohlcv_panel


def make_alpha_panel(
    n_days: int = 500,
    n_assets: int = 50,
    *,
    seed: int = 42,
    with_fundamentals: bool = True,
) -> dict[str, pd.DataFrame]:
    """Panel tailored for alpha computation tests.

    Uses an Ornstein-Uhlenbeck (mean-reverting) process for prices to keep
    cross-sectional rankings varying over time. When ``with_fundamentals``
    is True, adds ``fund:roe``, ``fund:gross_profitability``, etc., needed
    by the 4 fundamental alphas.

    Args:
        n_days: Number of trading days (default 500).
        n_assets: Number of assets (default 50).
        seed: RNG seed.
        with_fundamentals: Whether to add fund:* columns.

    Returns:
        dict of column-name → DataFrame.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    cols = [f"S{i}" for i in range(n_assets)]

    # OU process: mean-revert to 10.0
    prices = np.zeros((n_days, n_assets))
    prices[0] = 10.0 + rng.normal(0, 0.5, n_assets)
    mean_rev_speed = 0.05
    vol = 0.02
    for t in range(1, n_days):
        drift = mean_rev_speed * (10.0 - prices[t - 1])
        prices[t] = prices[t - 1] * (1 + drift + rng.normal(0, vol, n_assets))

    close = pd.DataFrame(prices, index=dates, columns=cols)
    high = close * (1 + np.abs(rng.normal(0, 0.005, close.shape)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, close.shape)))
    open_ = close.shift(1).fillna(close.iloc[0]) * (
        1 + rng.normal(0, 0.002, close.shape)
    )
    volume = pd.DataFrame(rng.uniform(1e6, 1e8, close.shape), index=dates, columns=cols)
    amount = pd.DataFrame(rng.uniform(1e7, 1e9, close.shape), index=dates, columns=cols)

    panel = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "vwap": close * (1 + rng.normal(0, 0.001, close.shape)),
        "returns": close.pct_change().fillna(0),
    }
    for w in (5, 10, 15, 20, 30, 50, 60):
        panel[f"adv{w}"] = volume.rolling(w).mean().fillna(volume.mean())

    if with_fundamentals:
        panel["fund:roe"] = pd.DataFrame(
            rng.uniform(0.05, 0.25, close.shape), index=dates, columns=cols
        )
        panel["fund:gross_profitability"] = pd.DataFrame(
            rng.uniform(0.05, 0.40, close.shape), index=dates, columns=cols
        )
        panel["fund:asset_growth"] = pd.DataFrame(
            rng.uniform(-0.10, 0.30, close.shape), index=dates, columns=cols
        )
        panel["fund:net_income"] = pd.DataFrame(
            rng.uniform(1e6, 1e9, close.shape), index=dates, columns=cols
        )
        panel["fund:shares_diluted"] = pd.DataFrame(
            rng.uniform(1e7, 1e10, close.shape), index=dates, columns=cols
        )

    return panel


def make_minimal_alpha_panel(
    n_days: int = 60,
    n_assets: int = 3,
    *,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """A minimal panel for quick alpha smoke tests."""
    return make_ohlcv_panel(n_days=n_days, n_assets=n_assets, seed=seed)