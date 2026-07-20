"""
{strategy_name} 数据加载和评估。

此文件定义了策略的数据加载和评估接口。
框架通过 load_data() 和 evaluate() 与策略交互。

Agent 不应修改此文件。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# 目标函数 (Agent 不改)
# ============================================================
GOAL_METRIC = "{goal_metric}"
GOAL_DIRECTION = "maximize"


# ============================================================
# 数据加载
# ============================================================
def load_data() -> dict:
    """加载策略数据。

    Returns:
        dict: 包含策略所需的所有数据
            - "prices": 价格数据 (DataFrame, index=date, columns=assets)
            - "factors": 因子数据 (可选)
            - "macro": 宏观数据 (可选)
    """
    # TODO: 实现数据加载
    # 示例:
    # prices = pd.read_csv("data/prices.csv", index_col=0, parse_dates=True)
    # return {{"prices": prices}}
    raise NotImplementedError("请实现 load_data()")


# ============================================================
# 因子计算
# ============================================================
def compute_factors(prices: pd.DataFrame, factor_exprs: list[dict]) -> pd.DataFrame:
    """计算因子值。

    Args:
        prices: 价格数据 (index=date, columns=assets)
        factor_exprs: 因子表达式列表
            [{{"factor_name": "mom_20d", "factor_code": "ts_return(close, 20)"}}]

    Returns:
        DataFrame: 因子值 (index=date, columns=(date, asset, factor_name))
    """
    # TODO: 实现因子计算
    # 示例:
    # factors = {{}}
    # for expr in factor_exprs:
    #     name = expr["factor_name"]
    #     code = expr["factor_code"]
    #     factors[name] = eval_factor(code, prices)
    # return pd.DataFrame(factors)
    raise NotImplementedError("请实现 compute_factors()")


# ============================================================
# 策略评估
# ============================================================
def evaluate(params: dict, factor_exprs: list[dict],
             factor_weight_method: str, data: dict) -> dict:
    """评估策略表现。

    Args:
        params: 策略参数 (PARAMS from strategy.py)
            - "top_n": 持仓数量
            - "max_weight": 单资产最大权重
            - "rebalance_freq": 调仓频率
            - ...
        factor_exprs: 因子表达式列表 (FACTOR_EXPRS from strategy.py)
            [{{"factor_name": "mom_20d", "factor_code": "ts_return(close, 20)", "category": "momentum"}}]
        factor_weight_method: 因子权重方式
            - "equal": 等权
            - "inv_vol": 逆波动率
            - "ic_ir": IC/IR 加权
            - "risk_parity": 风险平价
        data: load_data() 返回的数据

    Returns:
        dict: 评估指标
            - GOAL_METRIC: 目标函数值 (必须)
            - "sharpe": Sharpe 比率
            - "max_dd": 最大回撤 (负数)
            - "ann_return": 年化收益率
            - "ann_vol": 年化波动率
            - "sortino": Sortino 比率
            - "turnover": 年化换手率
    """
    # TODO: 实现评估逻辑
    # 示例:
    # prices = data["prices"]
    # factors = compute_factors(prices, factor_exprs)
    #
    # # 1. 计算因子加权分数
    # if factor_weight_method == "equal":
    #     weights = {{f: 1.0 / len(factor_exprs) for f in factors}}
    # elif factor_weight_method == "inv_vol":
    #     # 逆波动率加权
    #     ...
    #
    # # 2. 选择 top_n 资产
    # top_assets = scores.nlargest(params.get("top_n", 10))
    #
    # # 3. 计算组合收益
    # portfolio_returns = (top_assets * weights).sum(axis=1)
    #
    # # 4. 计算指标
    # nav = (1 + portfolio_returns).cumprod()
    # calmar = ann_return / abs(max_dd)
    #
    # return {{
    #     GOAL_METRIC: calmar,
    #     "sharpe": sharpe,
    #     "max_dd": max_dd,
    #     "ann_return": ann_return,
    #     "ann_vol": ann_vol,
    #     "turnover": turnover,
    # }}
    raise NotImplementedError("请实现 evaluate()")


# ============================================================
# 辅助函数
# ============================================================
def _ann_return(nav: pd.Series, freq: int = 252) -> float:
    """计算年化收益率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change().dropna()
    if rets.empty:
        return 0.0
    n_years = len(rets) / freq
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    return float((1 + total_ret) ** (1 / max(n_years, 1e-9)) - 1)


def _max_drawdown(nav: pd.Series) -> float:
    """计算最大回撤 (负数)。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    cummax = nav.cummax()
    dd = (nav / cummax - 1)
    return float(dd.min())


def _sharpe(nav: pd.Series, freq: int = 252) -> float:
    """计算 Sharpe 比率。"""
    vol = _ann_vol(nav, freq)
    if vol == 0:
        return 0.0
    return _ann_return(nav, freq) / vol


def _ann_vol(nav: pd.Series, freq: int = 252) -> float:
    """计算年化波动率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change().dropna()
    return float(rets.std() * np.sqrt(freq)) if not rets.empty else 0.0


def _sortino(nav: pd.Series, freq: int = 252) -> float:
    """计算 Sortino 比率。"""
    if nav.empty:
        return 0.0
    rets = nav.pct_change().dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    dd = float(downside.std() * np.sqrt(freq)) if not downside.empty else 0.0
    if dd == 0:
        return 0.0
    return _ann_return(nav, freq) / dd


def _turnover(weights: pd.DataFrame) -> float:
    """计算年化换手率。"""
    if weights.empty or len(weights) < 2:
        return 0.0
    changes = weights.diff().abs().sum(axis=1)
    return float(changes.mean() * 252)
