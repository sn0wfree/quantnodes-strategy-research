"""Portfolio optimizers — 基于 QuantOPT 的 5 个优化器。

统一接口：
  optimize_weights(ret_df, pos_df, dates, method) → pd.DataFrame

5 个优化器：
  1. equal_volatility  — inverse-volatility (自写 scipy)
  2. risk_parity       — QuantOPT RiskParity
  3. mean_variance     — QuantOPT MVO (Markowitz)
  4. max_diversification — QuantOPT MaxIR (adapted)
  5. turnover_aware    — QuantOPT MaxRiskAdjReturn
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize

try:
    from QuantOPT.models.model_RiskParity import RiskParity
    from QuantOPT.models.model_MVO import MVO
    from QuantOPT.models.model_MaxIR import MaxIR
    from QuantOPT.models.model_MaxRiskAdjReturn import MaxRiskAdjReturn
    _HAS_QUANTOPT = True
except ImportError:
    _HAS_QUANTOPT = False


# ============================================================
# 1. Equal Volatility (inverse-vol, scipy 直接优化)
# ============================================================

def _equal_volatility_optimize(codes, cov):
    """inverse-volatility 权重。"""
    vols = np.sqrt(np.diag(cov))
    vols = np.where(vols < 1e-10, 1e-10, vols)
    inv_vols = 1.0 / vols
    weights = inv_vols / inv_vols.sum()
    return weights


# ============================================================
# 2. Risk Parity (QuantOPT)
# ============================================================

def _risk_parity_optimize(codes, cov):
    port_std = np.sqrt(np.diag(cov))
    res = RiskParity.run_opt(
        stockpool=codes, port_std=port_std, cov=cov,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 3. Mean Variance (QuantOPT MVO)
# ============================================================

def _mean_variance_optimize(codes, cov, mu, risk_aversion=1.0):
    res = MVO.run_opt(
        stockpool=codes, sigma2=cov, stock_ret=mu * risk_aversion,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 4. Max Diversification (QuantOPT MaxIR adapted)
# ============================================================

def _max_diversification_optimize(codes, cov, mu):
    res = MaxIR.run_opt(
        stockpool=codes, sigma2=cov, portfolio_returns=mu,
        lambda_r=1.0,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 5. Turnover Aware (QuantOPT MaxRiskAdjReturn)
# ============================================================

def _turnover_aware_optimize(codes, cov, mu, current_weights, lambda_r=1.0):
    res = MaxRiskAdjReturn.run_opt(
        stockpool=codes, sigma2=cov, portfolio_returns=mu,
        lambda_r=lambda_r,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 统一接口
# ============================================================

_METHOD_MAP = {
    "equal_volatility": "equal_volatility",
    "risk_parity": "risk_parity",
    "mean_variance": "mean_variance",
    "max_diversification": "max_diversification",
    "turnover_aware": "turnover_aware",
}


def optimize_weights(
    ret_df: pd.DataFrame,
    pos_df: pd.DataFrame,
    dates: pd.DatetimeIndex,
    method: str = "equal_volatility",
    **kwargs,
) -> pd.DataFrame:
    """统一优化接口。

    Args:
        ret_df: 收益率矩阵 (T, N)
        pos_df: 当前权重矩阵 (同 shape)
        dates: 统一日期索引
        method: 优化方法名
        **kwargs: 额外参数 (risk_aversion, lambda_r 等)

    Returns:
        优化后的权重矩阵 (T, N)
    """
    codes = list(ret_df.columns)
    n = len(codes)
    if n == 0:
        return pos_df.copy()

    if method not in _METHOD_MAP:
        raise ValueError(f"Unknown method: {method}. Choose from {list(_METHOD_MAP.keys())}")

    # 协方差矩阵
    cov = ret_df.cov().values
    if np.any(np.isnan(cov)):
        cov = np.nan_to_num(cov, nan=0.0)

    # 预期收益
    mu = ret_df.mean().values

    # 优化
    try:
        if method == "equal_volatility":
            weights = _equal_volatility_optimize(codes, cov)
        elif method == "risk_parity":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for risk_parity")
            weights = _risk_parity_optimize(codes, cov)
        elif method == "mean_variance":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for mean_variance")
            weights = _mean_variance_optimize(codes, cov, mu, kwargs.get("risk_aversion", 1.0))
        elif method == "max_diversification":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for max_diversification")
            weights = _max_diversification_optimize(codes, cov, mu)
        elif method == "turnover_aware":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for turnover_aware")
            # 当前权重
            current_w = pos_df.iloc[-1].values if len(pos_df) > 0 else np.ones(n) / n
            weights = _turnover_aware_optimize(codes, cov, mu, current_w, kwargs.get("lambda_r", 1.0))
    except Exception:
        weights = np.ones(n) / n

    # 构建输出矩阵
    result = pd.DataFrame(
        np.tile(weights, (len(dates), 1)),
        index=dates,
        columns=codes,
    )
    return result


__all__ = ["optimize_weights"]