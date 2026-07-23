"""通用回测引擎 — BaseStrategy + StrategyEngine。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/strategy_engine.py。
精简版：移除 ETF 特定依赖，使用本地 metrics。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .backtest_config import (
    CostConfig,
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)
from .backtest_utils import calculate_turnover, generate_rebalance_dates
from .metrics import extended_metrics


# ============================================================
# 1. 策略基类
# ============================================================
class BaseStrategy:
    """所有策略的基类. 只需重写 compute_weights."""

    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]:
        """调仓日: 返回 {code: weight}. 必须实现."""
        raise NotImplementedError

    def on_risk_check(
        self,
        weights: dict[str, float],
        nav_history: pd.Series,
        date: pd.Timestamp,
    ) -> dict[str, float]:
        """可选: 自定义风控. 默认不做."""
        return weights


# ============================================================
# 2. 结果
# ============================================================
@dataclass
class BacktestResult:
    nav_daily: pd.Series
    weights_history: list[tuple[pd.Timestamp, dict[str, float]]] = field(default_factory=list)
    rebalance_dates: list[pd.Timestamp] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ============================================================
# 3. 通用引擎
# ============================================================
class StrategyEngine:
    """通用回测引擎.

    风控优先级:
      1. 策略实现了 on_risk_check → 用策略的
      2. 策略没实现 → 用引擎配置的 VT/TF/SL
      3. 都没有 → 不做风控
    """

    def __init__(
        self,
        vol_targeting: VolTargetingConfig | None = None,
        trend_filter: TrendFilterConfig | None = None,
        stop_loss: StopLossConfig | None = None,
    ):
        self.vol_targeting = vol_targeting
        self.trend_filter = trend_filter
        self.stop_loss = stop_loss
        self._has_risk_callback = None

    def run(
        self,
        price_panel: pd.DataFrame,
        strategy: BaseStrategy,
        rebal_freq: str = "M",
        min_history: int = 144,
        cost: CostConfig | None = None,
    ) -> BacktestResult:
        cost = cost or CostConfig(enabled=False)
        dates = price_panel.index

        # 检查策略是否自定义了风控
        if self._has_risk_callback is None:
            self._has_risk_callback = (
                type(strategy).on_risk_check is not BaseStrategy.on_risk_check
            )

        # 调仓日
        rebal_dates_list = generate_rebalance_dates(dates, rebal_freq, min_history)
        rebal_set = set(rebal_dates_list)

        # NAV 循环
        nav = np.ones(len(dates))
        prev_w: dict[str, float] = {}
        w_hist: list[tuple[pd.Timestamp, dict[str, float]]] = []

        for i, date in enumerate(dates):
            if date in rebal_set and i >= min_history:
                nav_s = pd.Series(nav[:i + 1], index=dates[:i + 1])
                new_w = strategy.compute_weights(date, price_panel, nav_s)

                # 风控: 策略回调 > 引擎配置
                if self._has_risk_callback:
                    new_w = strategy.on_risk_check(new_w, nav_s, date)
                else:
                    new_w = self._apply_engine_risk(new_w, nav_s, date)

                # 成本
                if cost.enabled and prev_w:
                    t = calculate_turnover(prev_w, new_w)
                    nav[i] = nav[i - 1] * (1 - t * cost.cost_rate())
                elif i > 0:
                    nav[i] = nav[i - 1]

                prev_w = new_w
                w_hist.append((date, dict(new_w)))
            else:
                if i > 0 and prev_w:
                    dr = 0.0
                    for c, w in prev_w.items():
                        if c in price_panel.columns:
                            a, b = price_panel[c].iloc[i], price_panel[c].iloc[i - 1]
                            if not pd.isna(a) and not pd.isna(b) and b != 0:
                                dr += w * (a / b - 1)
                    nav[i] = nav[i - 1] * (1 + dr)
                else:
                    nav[i] = 1.0 if i == 0 else nav[i - 1]

        nav_s = pd.Series(nav, index=dates, name="nav")
        metrics = extended_metrics(nav_s)

        return BacktestResult(
            nav_daily=nav_s,
            weights_history=w_hist,
            rebalance_dates=[d for d, _ in w_hist],
            metrics=metrics,
        )

    def _apply_engine_risk(self, weights, nav_history, date):
        """引擎默认风控: VT → TF → SL."""
        if not weights:
            return weights

        # 波动率目标
        if (self.vol_targeting and self.vol_targeting.enabled
                and len(nav_history) >= self.vol_targeting.lookback):
            rets = nav_history.iloc[-self.vol_targeting.lookback:].pct_change(fill_method=None).dropna()
            if len(rets) >= 10:
                vol = rets.std() * np.sqrt(252)
                if vol > 0:
                    s = self.vol_targeting.target_vol / vol
                    s = max(self.vol_targeting.min_scale, min(self.vol_targeting.max_scale, s))
                    weights = {k: v * s for k, v in weights.items()}

        # 趋势过滤
        if (self.trend_filter and self.trend_filter.enabled
                and len(nav_history) >= self.trend_filter.ma_window):
            ma = nav_history.iloc[-self.trend_filter.ma_window:].mean()
            if nav_history.iloc[-1] < ma:
                equity_exposure = self.trend_filter.bear_exposure
                weights = {k: v * equity_exposure for k, v in weights.items()}

        # 硬止损
        if (self.stop_loss and self.stop_loss.enabled
                and len(nav_history) >= 2):
            peak = nav_history.max()
            dd = nav_history.iloc[-1] / peak - 1.0
            if dd < self.stop_loss.threshold:
                weights = {}

        return weights
