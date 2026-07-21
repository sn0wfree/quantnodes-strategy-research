"""为 fundamental alpha 提供 mock 数据 fixtures.

让 fundamental_* alpha (4 个) 在不需要真实 Tushare 数据的情况下
能正常跑完 — 用合理范围的合成数据替代。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_fundamentals_panel(
    n_stocks: int = 5,
    n_days: int = 252,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """构造带 fund:* 数据的测试面板.

    提供所有 fundamental_* alpha 需要的列:
    - fund:roe (fundamental_roe)
    - fund:gross_profitability (fundamental_gross_profitability)
    - fund:asset_growth (fundamental_asset_growth)
    - fund:net_income, fund:shares_diluted (fundamental_earnings_yield)
    - close, volume (用于 earnings_yield 的 market_cap)

    Returns:
        dict[str, pd.DataFrame]: 面板 dict
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    cols = [f"S{i}" for i in range(n_stocks)]

    prices = pd.DataFrame(
        rng.uniform(10, 100, (n_days, n_stocks)),
        index=dates, columns=cols,
    )

    # 季度报告日 (90 天一次, 取季末值持有至下个季度)
    # 简化: 用同一日期全市场一致的 quarterly values
    quarterly_dates = pd.bdate_range("2024-01-01", periods=4, freq="90D")
    quarterly_dates = quarterly_dates[:n_days]

    def quarterly_to_panel(values_per_quarter, default=0.0):
        """把季末值扩展到每日面板."""
        df = pd.DataFrame(
            np.tile(values_per_quarter, (n_days, 1)),
            index=dates, columns=cols,
        )
        # NaN 模拟缺失的报告
        mask = rng.random(df.shape) < 0.1
        df = df.mask(mask, np.nan)
        # 让 quarter 之间的差为 linear interpolation
        df = df.ffill().bfill()
        return df.fillna(default)

    # ROE: 范围 [-0.05, 0.20]
    roe_q = rng.uniform(-0.05, 0.20, (len(cols),)) * 4  # 季报 4 次
    roe_panel = pd.DataFrame(
        rng.uniform(-0.05, 0.20, (n_days, n_stocks)),
        index=dates, columns=cols,
    )
    # 每股票单一 ROE 值 (模拟"最新季报"是已知的, 历史 ROE 会有变化)
    for i, c in enumerate(cols):
        base_roe = rng.uniform(0.05, 0.20)
        roe_panel[c] = base_roe + rng.normal(0, 0.02, n_days)

    # Gross profitability: 范围 [0.05, 0.40]
    gp_panel = pd.DataFrame(
        rng.uniform(0.05, 0.40, (n_days, n_stocks)),
        index=dates, columns=cols,
    )

    # Asset growth: 范围 [-0.10, 0.30] YoY
    ag_panel = pd.DataFrame(
        rng.uniform(-0.10, 0.30, (n_days, n_stocks)),
        index=dates, columns=cols,
    )

    # Net income: 季末值 (模拟季报)
    net_income_q = rng.uniform(1e6, 1e9, (len(cols),))
    net_income_panel = pd.DataFrame(
        np.tile(net_income_q, (n_days, 1)),
        index=dates, columns=cols,
    )

    # Shares diluted: 稳定不变
    shares_q = rng.uniform(1e7, 1e10, (len(cols),))
    shares_panel = pd.DataFrame(
        np.tile(shares_q, (n_days, 1)),
        index=dates, columns=cols,
    )

    return {
        # 标准 OHLCV
        "open": prices * 0.99,
        "high": prices * 1.01,
        "low": prices * 0.97,
        "close": prices,
        "volume": pd.DataFrame(
            rng.uniform(1e6, 1e8, (n_days, n_stocks)),
            index=dates, columns=cols,
        ),
        "amount": pd.DataFrame(
            rng.uniform(1e7, 1e9, (n_days, n_stocks)),
            index=dates, columns=cols,
        ),
        "vwap": prices,
        "returns": prices.pct_change().fillna(0),
        # 基本面数据
        "fund:roe": roe_panel,
        "fund:gross_profitability": gp_panel,
        "fund:asset_growth": ag_panel,
        "fund:net_income": net_income_panel,
        "fund:shares_diluted": shares_panel,
    }


def make_market_benchmark_panel(
    n_stocks: int = 5,
    n_days: int = 252,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """构造带 academic benchmark 数据的测试面板.

    Academic alphas 通常只需 close/vol/ohlcv — 这里构造标准 panel.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    cols = [f"S{i}" for i in range(n_stocks)]

    close = pd.DataFrame(
        rng.uniform(10, 50, (n_days, n_stocks)),
        index=dates, columns=cols,
    )

    return {
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": pd.DataFrame(
            rng.uniform(1e6, 1e8, (n_days, n_stocks)),
            index=dates, columns=cols,
        ),
        "amount": pd.DataFrame(
            rng.uniform(1e7, 1e9, (n_days, n_stocks)),
            index=dates, columns=cols,
        ),
        "vwap": close,
    }
