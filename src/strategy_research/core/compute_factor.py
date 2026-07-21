"""因子计算工具。

支持时序算子、截面算子和数学算子的因子值计算。
算子借鉴自 QuantNodes/research/quant_alpha/operator_vocab/。
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np
import pandas as pd


# ============================================================
# 时序算子 (per-asset)
# ============================================================

def ts_return(series: pd.Series, window: int) -> pd.Series:
    """N 期收益率。"""
    return series.pct_change(window, fill_method=None)


def ts_std(series: pd.Series, window: int) -> pd.Series:
    """N 期标准差。"""
    return series.rolling(window).std()


def ts_corr(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """N 期相关系数。"""
    return x.rolling(window).corr(y)


def ts_rank(series: pd.Series, window: int) -> pd.Series:
    """N 期排名百分比。"""
    return series.rolling(window).rank(pct=True)


def delay(series: pd.Series, periods: int) -> pd.Series:
    """滞后 N 期。"""
    return series.shift(periods)


def delta(series: pd.Series, periods: int) -> pd.Series:
    """N 期变化量。"""
    return series - series.shift(periods)


def ts_max(series: pd.Series, window: int) -> pd.Series:
    """N 期最大值。"""
    return series.rolling(window).max()


def ts_min(series: pd.Series, window: int) -> pd.Series:
    """N 期最小值。"""
    return series.rolling(window).min()


def ts_mean(series: pd.Series, window: int) -> pd.Series:
    """N 期均值。"""
    return series.rolling(window).mean()


def ts_sum(series: pd.Series, window: int) -> pd.Series:
    """N 期求和。"""
    return series.rolling(window).sum()


def ts_skew(series: pd.Series, window: int) -> pd.Series:
    """N 期偏度。"""
    return series.rolling(window).skew()


def ts_kurt(series: pd.Series, window: int) -> pd.Series:
    """N 期峰度。"""
    return series.rolling(window).kurt()


# ============================================================
# 新增时序算子
# ============================================================

def ts_median(series: pd.Series, window: int) -> pd.Series:
    """N 期中位数。"""
    return series.rolling(window).median()


def ts_var(series: pd.Series, window: int) -> pd.Series:
    """N 期方差。"""
    return series.rolling(window).var()


def ts_prod(series: pd.Series, window: int) -> pd.Series:
    """N 期乘积。"""
    return series.rolling(window).apply(np.prod, raw=True)


def ts_argmax(series: pd.Series, window: int) -> pd.Series:
    """N 期最大值位置。"""
    return series.rolling(window).apply(np.argmax, raw=True)


def ts_argmin(series: pd.Series, window: int) -> pd.Series:
    """N 期最小值位置。"""
    return series.rolling(window).apply(np.argmin, raw=True)


def ts_cov(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """N 期协方差。"""
    return x.rolling(window).cov(y)


def ts_pct_change(series: pd.Series, periods: int) -> pd.Series:
    """N 期百分比变化。"""
    return series.pct_change(periods, fill_method=None)


def ts_zscore(series: pd.Series, window: int) -> pd.Series:
    """N 期滚动 z-score。"""
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mean) / std


def ts_decay_linear(series: pd.Series, window: int) -> pd.Series:
    """线性衰减加权 MA。"""
    weights = np.arange(1, window + 1, dtype=float)
    weights = weights / weights.sum()

    def _apply(x):
        return np.dot(x, weights)

    return series.rolling(window).apply(_apply, raw=True)


def ts_decay_exp(series: pd.Series, halflife: int) -> pd.Series:
    """指数衰减加权 MA。"""
    return series.ewm(halflife=halflife).mean()


# ============================================================
# 扩展窗口算子
# ============================================================

def expanding_sum(series: pd.Series) -> pd.Series:
    """扩展窗口求和。"""
    return series.expanding().sum()


def expanding_mean(series: pd.Series) -> pd.Series:
    """扩展窗口均值。"""
    return series.expanding().mean()


def expanding_max(series: pd.Series) -> pd.Series:
    """扩展窗口最大值。"""
    return series.expanding().max()


def expanding_min(series: pd.Series) -> pd.Series:
    """扩展窗口最小值。"""
    return series.expanding().min()


# ============================================================
# EWM 算子
# ============================================================

def ewm_mean(series: pd.Series, span: int = 20) -> pd.Series:
    """EWM 均值。"""
    return series.ewm(span=span).mean()


def ewm_std(series: pd.Series, span: int = 20) -> pd.Series:
    """EWM 标准差。"""
    return series.ewm(span=span).std()


def ewm_corr(x: pd.Series, y: pd.Series, span: int = 20) -> pd.Series:
    """EWM 相关系数。"""
    return x.ewm(span=span).corr(y)


# ============================================================
# 截面算子 (cross-section)
# ============================================================

def rank(series: pd.Series) -> pd.Series:
    """截面排名 (0-1)。"""
    return series.rank(pct=True)


def zscore(series: pd.Series) -> pd.Series:
    """截面 z-score。"""
    mean = series.mean()
    std = series.std()
    if std == 0:
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def scale(series: pd.Series) -> pd.Series:
    """截面缩放 (和=1)。"""
    total = series.abs().sum()
    if total == 0:
        return pd.Series(0.0, index=series.index)
    return series / total


def winsorize(series: pd.Series, n: float = 3.0) -> pd.Series:
    """截面缩尾。"""
    std = series.std()
    mean = series.mean()
    lower = mean - n * std
    upper = mean + n * std
    return series.clip(lower, upper)


# ============================================================
# 新增截面算子
# ============================================================

def neutralize(series: pd.Series, group: pd.Series) -> pd.Series:
    """行业中性化 (减去组均值)。"""
    return series - series.groupby(group).transform('mean')


def neutralize_market(series: pd.Series) -> pd.Series:
    """市场中性化 (减去均值)。"""
    return series - series.mean()


def group_norm(series: pd.Series, group: pd.Series) -> pd.Series:
    """分组标准化。"""
    return series.groupby(group).transform(lambda x: (x - x.mean()) / x.std())


def orthogonalize(series: pd.Series, reference: pd.Series) -> pd.Series:
    """正交化 (去除参考因子影响)。"""
    corr = series.corr(reference)
    if reference.std() == 0:
        return series
    return series - (corr * reference.std() / series.std()) * reference


def mad(series: pd.Series) -> pd.Series:
    """中位数绝对偏差 (x1.4826)。"""
    median = series.median()
    return 1.4826 * (series - median).abs()


def ic(series: pd.Series, target: pd.Series) -> float:
    """Pearson IC (信息系数)。"""
    return series.corr(target)


def rank_ic(series: pd.Series, target: pd.Series) -> float:
    """Spearman Rank IC。"""
    return series.rank().corr(target.rank())


def cross_sectional_mean(series: pd.Series) -> pd.Series:
    """截面均值。"""
    return pd.Series(series.mean(), index=series.index)


def cross_sectional_std(series: pd.Series) -> pd.Series:
    """截面标准差。"""
    return pd.Series(series.std(), index=series.index)


# ============================================================
# 新增截面算子 (高级)
# ============================================================

def cs_quantile_clip(df: pd.DataFrame, q_low: float = 0.025, q_high: float = 0.975) -> pd.DataFrame:
    """截面分位数剪裁: 把每行 (截面) 异常值剪裁到 [q_low, q_high] 分位数。"""
    def _clip(row):
        if row.notna().sum() < 2:
            return row
        lo = row.quantile(q_low)
        hi = row.quantile(q_high)
        return row.clip(lo, hi)
    return df.apply(_clip, axis=1)


def cs_pct_pos(df: pd.DataFrame) -> pd.DataFrame:
    """截面正值占比: 每行的 (x > 0) 比例。"""
    return (df > 0).sum(axis=1) / df.notna().sum(axis=1)


# ============================================================
# 新增时序算子 (高级)
# ============================================================

def ts_centralization(series: pd.Series, window: int) -> pd.Series:
    """滚动去均值: x - rolling_mean(x, window)。"""
    return series - series.rolling(window, min_periods=window).mean()


def ts_standardization(series: pd.Series, window: int) -> pd.Series:
    """滚动标准化: (x - rolling_mean) / rolling_std。"""
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std


def ts_entropy(series: pd.Series, window: int, bins: int = 10) -> pd.Series:
    """滚动信息熵 (Shannon): -sum(p * log(p))."""
    def _entropy(x):
        if np.isnan(x).all():
            return np.nan
        valid = x[~np.isnan(x)]
        if len(valid) < 2:
            return np.nan
        hist, _ = np.histogram(valid, bins=bins, density=False)
        total = hist.sum()
        if total == 0:
            return np.nan
        p = hist / total
        p = p[p > 0]
        return float(-(p * np.log(p)).sum())

    return series.rolling(window, min_periods=window).apply(_entropy, raw=True)


def ts_pct_pos(series: pd.Series, window: int) -> pd.Series:
    """滚动正值占比: 窗口内 x > 0 的比例。"""
    return series.rolling(window, min_periods=window).apply(
        lambda x: (x > 0).sum() / len(x) if len(x) > 0 else np.nan,
        raw=True,
    )


def ts_count_pos(series: pd.Series, window: int) -> pd.Series:
    """滚动正计数: 窗口内 x > 0 的个数。"""
    return series.rolling(window, min_periods=window).apply(
        lambda x: int((x > 0).sum()), raw=True,
    )


def ts_count_neg(series: pd.Series, window: int) -> pd.Series:
    """滚动负计数: 窗口内 x < 0 的个数。"""
    return series.rolling(window, min_periods=window).apply(
        lambda x: int((x < 0).sum()), raw=True,
    )


def ts_max_min_diff(series: pd.Series, window: int) -> pd.Series:
    """滚动 max-min 范围: rolling_max - rolling_min。"""
    return (
        series.rolling(window, min_periods=window).max()
        - series.rolling(window, min_periods=window).min()
    )


def ts_quantile_range(series: pd.Series, q_low: float, q_high: float, window: int) -> pd.Series:
    """滚动分位差: rolling_quantile(q_high) - rolling_quantile(q_low)."""
    high = series.rolling(window, min_periods=window).quantile(q_high)
    low = series.rolling(window, min_periods=window).quantile(q_low)
    return high - low


def ts_decay_custom(series: pd.Series, window: int, weights: np.ndarray) -> pd.Series:
    """自定义权重滚动加权平均: dot(weights, window)。"""
    weights = np.asarray(weights, dtype=float)
    if len(weights) != window:
        raise ValueError(f"weights length ({len(weights)}) must equal window ({window})")
    weights = weights / weights.sum()

    def _apply(x):
        valid = x[~np.isnan(x)]
        if len(valid) < window:
            return np.nan
        return float(np.dot(valid, weights))

    return series.rolling(window, min_periods=window).apply(_apply, raw=True)


# ============================================================
# 稳健统计时序算子
# ============================================================

def ts_iqr(series: pd.Series, window: int) -> pd.Series:
    """滚动四分位距: Q75 - Q25 (IQR)。"""
    q75 = series.rolling(window, min_periods=window).quantile(0.75)
    q25 = series.rolling(window, min_periods=window).quantile(0.25)
    return q75 - q25


def ts_median_abs_dev(series: pd.Series, window: int) -> pd.Series:
    """滚动中位绝对偏差: median(|x - median(x)|)。"""
    def _mad(x):
        valid = x[~np.isnan(x)]
        if len(valid) < window:
            return np.nan
        med = np.median(valid)
        return float(np.median(np.abs(valid - med)))

    return series.rolling(window, min_periods=window).apply(_mad, raw=True)


def ts_trim_mean(series: pd.Series, window: int, pct: float = 0.1) -> pd.Series:
    """滚动截尾均值: 去掉头尾各 pct 部分后取平均。"""
    def _trim(x):
        valid = x[~np.isnan(x)]
        if len(valid) < window:
            return np.nan
        sorted_x = np.sort(valid)
        k = int(len(sorted_x) * pct)
        if 2 * k >= len(sorted_x):
            return float(np.mean(sorted_x))
        return float(np.mean(sorted_x[k:len(sorted_x) - k]))

    return series.rolling(window, min_periods=window).apply(_trim, raw=True)


def ts_huber_mean(series: pd.Series, window: int, k: float = 1.345) -> pd.Series:
    """Huber 鲁棒均值: 使用 Huber 权重迭代重新加权, k 是 Huber 阈值。"""
    from scipy import optimize as _opt  # 用 scipy 最小化; 实际也可手写 50 行

    def _huber(x):
        valid = x[~np.isnan(x)]
        n = len(valid)
        if n < window:
            return np.nan
        mu = float(np.mean(valid))
        for _ in range(50):
            r = valid - mu
            mad = np.median(np.abs(r))
            if mad < 1e-10:
                break
            sigma = mad / 0.6745  # 1/(Q(0.75) 标准 sigma 估计)
            w = np.where(np.abs(r / sigma) <= k, 1.0, k / (np.abs(r / sigma)))
            w = np.nan_to_num(w, nan=0.0)
            if w.sum() == 0:
                break
            mu_new = float((w * valid).sum() / w.sum())
            if abs(mu_new - mu) < 1e-9:
                mu = mu_new
                break
            mu = mu_new
        return mu

    return series.rolling(window, min_periods=window).apply(_huber, raw=True)


# ============================================================
# 数学算子
# ============================================================

def abs_op(series: pd.Series) -> pd.Series:
    """绝对值。"""
    return series.abs()


def log(series: pd.Series) -> pd.Series:
    """对数。"""
    return np.log(series)


def sign(series: pd.Series) -> pd.Series:
    """符号函数。"""
    return np.sign(series)


def sqrt(series: pd.Series) -> pd.Series:
    """平方根。"""
    return np.sqrt(series)


def clip(series: pd.Series, lower: float = -np.inf, upper: float = np.inf) -> pd.Series:
    """截断。"""
    return series.clip(lower, upper)


def fill_null(series: pd.Series, value: float = 0.0) -> pd.Series:
    """填充空值。"""
    return series.fillna(value)


def add(f1: pd.Series, f2: pd.Series) -> pd.Series:
    """加法。"""
    return f1 + f2


def sub(f1: pd.Series, f2: pd.Series) -> pd.Series:
    """减法。"""
    return f1 - f2


def mul(f1: pd.Series, f2: pd.Series) -> pd.Series:
    """乘法。"""
    return f1 * f2


def div(f1: pd.Series, f2: pd.Series) -> pd.Series:
    """除法。"""
    return f1 / f2


def where(condition: pd.Series, true_val: pd.Series, false_val: pd.Series) -> pd.Series:
    """条件选择。"""
    return pd.Series(np.where(condition, true_val, false_val), index=condition.index)


def weighted_sum(factors: list[pd.Series], weights: list[float]) -> pd.Series:
    """加权求和。"""
    result = pd.Series(0.0, index=factors[0].index)
    for f, w in zip(factors, weights):
        result = result + f * w
    return result


def combine(f1: pd.Series, f2: pd.Series, method: str = "add") -> pd.Series:
    """组合两个因子。"""
    if method == "add":
        return f1 + f2
    elif method == "sub":
        return f1 - f2
    elif method == "mul":
        return f1 * f2
    elif method == "div":
        return f1 / f2
    elif method == "max":
        return pd.concat([f1, f2], axis=1).max(axis=1)
    elif method == "min":
        return pd.concat([f1, f2], axis=1).min(axis=1)
    else:
        raise ValueError(f"未知组合方法: {method}")


def signed_power(series: pd.Series, p: float) -> pd.Series:
    """保留符号的幂运算: sign(x) * abs(x)^p。"""
    return np.sign(series) * np.abs(series) ** p


def safe_div(a: pd.Series, b: pd.Series, eps: float = 1e-12) -> pd.Series:
    """安全除法: |b|<eps → NaN, 否则 a/b。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(np.abs(b) < eps, np.nan, a / np.where(np.abs(b) < eps, 1.0, b))
    if isinstance(a, pd.Series):
        result = pd.Series(result, index=a.index)
    return result


# ============================================================
# 算子注册表
# ============================================================

OPERATORS = {
    # 时序算子 (基础)
    "ts_return": ts_return,
    "ts_std": ts_std,
    "ts_corr": ts_corr,
    "ts_rank": ts_rank,
    "delay": delay,
    "delta": delta,
    "ts_max": ts_max,
    "ts_min": ts_min,
    "ts_mean": ts_mean,
    "ts_sum": ts_sum,
    "ts_skew": ts_skew,
    "ts_kurt": ts_kurt,

    # 时序算子 (新增)
    "ts_median": ts_median,
    "ts_var": ts_var,
    "ts_prod": ts_prod,
    "ts_argmax": ts_argmax,
    "ts_argmin": ts_argmin,
    "ts_cov": ts_cov,
    "ts_pct_change": ts_pct_change,
    "ts_zscore": ts_zscore,
    "ts_decay_linear": ts_decay_linear,
    "ts_decay_exp": ts_decay_exp,

    # 时序算子 (新增高级)
    "ts_centralization": ts_centralization,
    "ts_standardization": ts_standardization,
    "ts_entropy": ts_entropy,
    "ts_pct_pos": ts_pct_pos,
    "ts_count_pos": ts_count_pos,
    "ts_count_neg": ts_count_neg,
    "ts_max_min_diff": ts_max_min_diff,
    "ts_quantile_range": ts_quantile_range,
    "ts_decay_custom": ts_decay_custom,

    # 稳健统计
    "ts_iqr": ts_iqr,
    "ts_median_abs_dev": ts_median_abs_dev,
    "ts_trim_mean": ts_trim_mean,
    "ts_huber_mean": ts_huber_mean,

    # 截面算子 (新增高级)
    "cs_quantile_clip": cs_quantile_clip,
    "cs_pct_pos": cs_pct_pos,

    # 扩展窗口算子
    "expanding_sum": expanding_sum,
    "expanding_mean": expanding_mean,
    "expanding_max": expanding_max,
    "expanding_min": expanding_min,

    # EWM 算子
    "ewm_mean": ewm_mean,
    "ewm_std": ewm_std,
    "ewm_corr": ewm_corr,

    # 截面算子 (基础)
    "rank": rank,
    "zscore": zscore,
    "scale": scale,
    "winsorize": winsorize,

    # 截面算子 (新增)
    "neutralize": neutralize,
    "neutralize_market": neutralize_market,
    "group_norm": group_norm,
    "orthogonalize": orthogonalize,
    "mad": mad,
    "ic": ic,
    "rank_ic": rank_ic,
    "cross_sectional_mean": cross_sectional_mean,
    "cross_sectional_std": cross_sectional_std,

    # 数学算子
    "abs": abs_op,
    "log": log,
    "sign": sign,
    "sqrt": sqrt,
    "clip": clip,
    "fill_null": fill_null,
    "add": add,
    "sub": sub,
    "mul": mul,
    "div": div,
    "where": where,
    "weighted_sum": weighted_sum,
    "combine": combine,
    "signed_power": signed_power,
    "safe_div": safe_div,

    # 比较算子
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "neq": lambda a, b: a != b,

    # 一元运算
    "neg": lambda a: -a,

    # pandas/numpy 兼容算子
    "sum": lambda series, window: series.rolling(window).sum(),
    "mean": lambda series, window: series.rolling(window).mean(),
    "std": lambda series, window: series.rolling(window).std(),
    "var": lambda series, window: series.rolling(window).var(),
    "min": lambda series, window: series.rolling(window).min(),
    "max": lambda series, window: series.rolling(window).max(),
    "median": lambda series, window: series.rolling(window).median(),
    "skew": lambda series, window: series.rolling(window).skew(),
    "kurt": lambda series, window: series.rolling(window).kurt(),
    "quantile": lambda series, window: series.rolling(window).quantile(0.5),
    "cumsum": lambda series: series.cumsum(),
    "cumprod": lambda series: series.cumprod(),
    "cummax": lambda series: series.cummax(),
    "cummin": lambda series: series.cummin(),
    "log1p": lambda series: np.log1p(series),
    "exp": lambda series: np.exp(series),
    "pow": lambda a, b: a ** b,
    "power": lambda a, b: a ** b,
    "minimum": lambda a, b: pd.DataFrame(np.minimum(a, b), index=a.index, columns=a.columns) if isinstance(a, pd.DataFrame) else np.minimum(a, b),
    "maximum": lambda a, b: pd.DataFrame(np.maximum(a, b), index=a.index, columns=a.columns) if isinstance(a, pd.DataFrame) else np.maximum(a, b),
    "clip_upper": lambda series, upper: series.clip(upper=upper),
    "clip_lower": lambda series, lower: series.clip(lower=lower),
    "shift": lambda series, periods: series.shift(periods),
    "diff": lambda series, periods: series.diff(periods),
    "pct_change": lambda series, periods=1: series.pct_change(periods, fill_method=None),
    "fillna": lambda series, value: series.fillna(value),
    "replace": lambda series, old, new: series.replace(old, new),
    "astype": lambda series, dtype: series.astype(dtype),
    "to_numpy": lambda series: series.to_numpy(),
    "to_df": lambda data, ref=None: data if isinstance(data, (pd.DataFrame, pd.Series)) else pd.DataFrame(data, index=ref.index, columns=ref.columns) if isinstance(ref, pd.DataFrame) and hasattr(data, 'shape') else pd.DataFrame(np.full(ref.shape, float(data)), index=ref.index, columns=ref.columns) if isinstance(ref, pd.DataFrame) else pd.DataFrame(data) if hasattr(data, '__len__') else pd.DataFrame(np.full((1,1), data)),
    "fmax": lambda a, b: pd.DataFrame(np.fmax(a, b), index=a.index, columns=a.columns) if isinstance(a, pd.DataFrame) else np.fmax(a, b),
    "fmin": lambda a, b: pd.DataFrame(np.fmin(a, b), index=a.index, columns=a.columns) if isinstance(a, pd.DataFrame) else np.fmin(a, b),
    "abs": lambda x: x.abs() if hasattr(x, 'abs') else np.abs(x),
    "not_": lambda x: ~x if hasattr(x, '__invert__') else (not x),
    "sign": lambda x: x.apply(np.sign) if isinstance(x, pd.DataFrame) else np.sign(x),
    "or_": lambda a, b: (a | b) if (hasattr(a, 'dtype') and a.dtype.kind == 'b' and hasattr(b, 'dtype') and b.dtype.kind == 'b') else np.logical_or(a, b),
    "and_": lambda a, b: (a & b) if (hasattr(a, 'dtype') and a.dtype.kind == 'b' and hasattr(b, 'dtype') and b.dtype.kind == 'b') else np.logical_and(a, b),
    "copy": lambda x: x.copy() if hasattr(x, 'copy') else x,
    "ones_like": lambda x: pd.DataFrame(np.ones_like(x.values), index=x.index, columns=x.columns) if isinstance(x, pd.DataFrame) else np.ones_like(x),
}


# ============================================================
# 表达式解析 (Pratt Parser)
# ============================================================

# Token 类型
TOKEN_NUMBER = "NUMBER"
TOKEN_IDENT = "IDENT"
TOKEN_LPAREN = "("
TOKEN_RPAREN = ")"
TOKEN_COMMA = ","
TOKEN_PLUS = "+"
TOKEN_MINUS = "-"
TOKEN_STAR = "*"
TOKEN_SLASH = "/"
TOKEN_POWER = "**"
TOKEN_EOF = "EOF"


class Token:
    """词法单元"""
    def __init__(self, type_: str, value: str):
        self.type = type_
        self.value = value

    def __repr__(self):
        return f"Token({self.type}, {self.value!r})"


def _tokenize(expr: str) -> list[Token]:
    """将表达式字符串分词为 Token 列表。"""
    tokens = []
    i = 0
    n = len(expr)

    while i < n:
        ch = expr[i]

        # 跳过空白
        if ch.isspace():
            i += 1
            continue

        # 数字 (整数或浮点数)
        if ch.isdigit() or (ch == '.' and i + 1 < n and expr[i + 1].isdigit()):
            start = i
            while i < n and (expr[i].isdigit() or expr[i] == '.'):
                i += 1
            # 科学计数法
            if i < n and expr[i] in ('e', 'E'):
                i += 1
                if i < n and expr[i] in ('+', '-'):
                    i += 1
                while i < n and expr[i].isdigit():
                    i += 1
            tokens.append(Token(TOKEN_NUMBER, expr[start:i]))
            continue

        # 标识符 (算子名或列名)
        if ch.isalpha() or ch == '_':
            start = i
            while i < n and (expr[i].isalnum() or expr[i] == '_'):
                i += 1
            tokens.append(Token(TOKEN_IDENT, expr[start:i]))
            continue

        # 运算符和标点
        if ch == '(':
            tokens.append(Token(TOKEN_LPAREN, ch))
            i += 1
        elif ch == ')':
            tokens.append(Token(TOKEN_RPAREN, ch))
            i += 1
        elif ch == ',':
            tokens.append(Token(TOKEN_COMMA, ch))
            i += 1
        elif ch == '+':
            tokens.append(Token(TOKEN_PLUS, ch))
            i += 1
        elif ch == '-':
            tokens.append(Token(TOKEN_MINUS, ch))
            i += 1
        elif ch == '*':
            if i + 1 < n and expr[i + 1] == '*':
                tokens.append(Token(TOKEN_POWER, '**'))
                i += 2
            else:
                tokens.append(Token(TOKEN_STAR, ch))
                i += 1
        elif ch == '/':
            tokens.append(Token(TOKEN_SLASH, ch))
            i += 1
        else:
            raise ValueError(f"未知字符: {ch!r} (位置 {i})")

    tokens.append(Token(TOKEN_EOF, ""))
    return tokens


class _Parser:
    """递归下降解析器 (Pratt Parser 风格)"""

    def __init__(self, tokens: list[Token], data: pd.DataFrame):
        self.tokens = tokens
        self.data = data
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def consume(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, type_: str) -> Token:
        tok = self.consume()
        if tok.type != type_:
            raise ValueError(f"期望 {type_}, 得到 {tok.type} ({tok.value!r})")
        return tok

    # expression := term (('+' | '-') term)*
    def parse_expression(self) -> pd.Series:
        left = self.parse_term()
        while self.peek().type in (TOKEN_PLUS, TOKEN_MINUS):
            op = self.consume()
            right = self.parse_term()
            if op.type == TOKEN_PLUS:
                left = left + right
            else:
                left = left - right
        return left

    # term := factor (('*' | '/') factor)*
    def parse_term(self) -> pd.Series:
        left = self.parse_factor()
        while self.peek().type in (TOKEN_STAR, TOKEN_SLASH):
            op = self.consume()
            right = self.parse_factor()
            if op.type == TOKEN_STAR:
                left = left * right
            else:
                left = safe_div(left, right) if 'safe_div' in OPERATORS else left / right
        return left

    # factor := ('+' | '-') factor | power
    def parse_factor(self) -> pd.Series:
        if self.peek().type == TOKEN_MINUS:
            self.consume()
            factor = self.parse_factor()
            return -factor
        if self.peek().type == TOKEN_PLUS:
            self.consume()
            return self.parse_factor()
        return self.parse_power()

    # power := primary ('**' factor)?
    def parse_power(self) -> pd.Series:
        left = self.parse_primary()
        if self.peek().type == TOKEN_POWER:
            self.consume()
            right = self.parse_factor()
            return left ** right
        return left

    # primary := NUMBER | IDENT | function_call | '(' expression ')'
    def parse_primary(self) -> pd.Series:
        tok = self.peek()

        # 数字
        if tok.type == TOKEN_NUMBER:
            self.consume()
            val = float(tok.value)
            # 如果是整数，转换为 int (用于窗口参数)
            if val == int(val):
                val = int(val)
            # 返回与数据同形状的常量 Series
            if not self.data.empty:
                return pd.Series(val, index=self.data.index, dtype=float if isinstance(val, float) else int)
            return pd.Series(val, dtype=float if isinstance(val, float) else int)

        # 括号表达式
        if tok.type == TOKEN_LPAREN:
            self.consume()
            result = self.parse_expression()
            self.expect(TOKEN_RPAREN)
            return result

        # 标识符 (函数调用或列名)
        if tok.type == TOKEN_IDENT:
            self.consume()
            name = tok.value

            # 检查是否是函数调用
            if self.peek().type == TOKEN_LPAREN:
                return self.parse_function_call(name)

            # 列名
            if name in self.data.columns:
                return self.data[name]

            raise ValueError(f"未知标识符: {name}")

        raise ValueError(f"意外的 token: {tok}")

    # function_call := IDENT '(' arg_list ')'
    def parse_function_call(self, name: str) -> pd.Series:
        self.expect(TOKEN_LPAREN)

        # 解析参数列表
        args = []
        if self.peek().type != TOKEN_RPAREN:
            args.append(self.parse_expression())
            while self.peek().type == TOKEN_COMMA:
                self.consume()
                args.append(self.parse_expression())

        self.expect(TOKEN_RPAREN)

        # 检查算子是否存在
        if name not in OPERATORS:
            raise ValueError(f"未知算子: {name}")

        op_func = OPERATORS[name]

        # 转换参数: 常量 Series -> 标量
        converted_args = []
        for arg in args:
            if isinstance(arg, pd.Series) and len(arg.unique()) == 1:
                # 常量 Series，提取标量值
                val = arg.iloc[0]
                if isinstance(val, (int, float)) and val == int(val):
                    converted_args.append(int(val))
                else:
                    converted_args.append(val)
            else:
                converted_args.append(arg)

        # 调用算子
        try:
            result = op_func(*converted_args)
            return result
        except Exception as e:
            raise ValueError(f"算子 {name} 执行失败: {e}")


# ============================================================
# 表达式解析 (兼容旧接口)
# ============================================================

def parse_expression(expr: str) -> tuple[str, list]:
    """解析因子表达式 (兼容旧接口)。

    示例:
        "ts_return(close, 20)" -> ("ts_return", ["close", 20])
        "rank(ts_return(close, 20))" -> ("rank", ["ts_return(close, 20)"])

    Returns:
        tuple: (op_name, args)
    """
    # 匹配函数调用模式: func(arg1, arg2, ...)
    match = re.match(r'^(\w+)\((.+)\)$', expr.strip())
    if not match:
        raise ValueError(f"无法解析表达式: {expr}")

    op_name = match.group(1)
    args_str = match.group(2)

    # 解析参数 (支持嵌套调用)
    args = _split_args(args_str)

    return op_name, args


def _split_args(args_str: str) -> list:
    """分割参数 (支持嵌套括号)。"""
    args = []
    depth = 0
    current = []

    for char in args_str:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(char)

    if current:
        args.append(''.join(current).strip())

    return args


def _parse_arg(arg: str, data: pd.DataFrame) -> pd.Series | float | int:
    """解析参数值。"""
    arg = arg.strip()

    # 尝试解析为数字
    try:
        return int(arg)
    except ValueError:
        pass
    try:
        return float(arg)
    except ValueError:
        pass

    # 尝试解析为列名
    if arg in data.columns:
        return data[arg]

    # 尝试解析为嵌套函数
    if '(' in arg and ')' in arg:
        return evaluate_expression(arg, data)

    raise ValueError(f"无法解析参数: {arg}")


# ============================================================
# 表达式求值
# ============================================================

def evaluate_expression(expr: str, data: pd.DataFrame) -> pd.Series:
    """计算因子表达式。

    支持:
    - 简单函数调用: ts_return(close, 20)
    - 嵌套函数调用: ts_std(ts_return(close, 1), 20)
    - 算术运算: close / ts_mean(close, 20) - 1
    - 括号: (close - open) / (close + open)
    - 一元运算: -ts_return(close, 1)

    Args:
        expr: 因子表达式
        data: 价格数据 (index=date, columns=assets)

    Returns:
        pd.Series: 因子值
    """
    # 尝试新解析器 (支持算术运算)
    try:
        tokens = _tokenize(expr)
        parser = _Parser(tokens, data)
        return parser.parse_expression()
    except Exception:
        pass

    # 回退到旧解析器 (仅支持函数调用)
    op_name, args = parse_expression(expr)

    # 检查算子是否存在
    if op_name not in OPERATORS:
        raise ValueError(f"未知算子: {op_name}")

    op_func = OPERATORS[op_name]

    # 解析参数
    parsed_args = []
    for arg in args:
        parsed_args.append(_parse_arg(arg, data))

    # 调用算子
    try:
        result = op_func(*parsed_args)
        return result
    except Exception as e:
        raise ValueError(f"算子 {op_name} 执行失败: {e}")


# ============================================================
# 主计算函数
# ============================================================

def compute_factor(
    factor_code: str,
    prices: pd.DataFrame,
    factor_name: str = "",
) -> pd.Series:
    """计算因子值。

    Args:
        factor_code: 因子表达式，如 "ts_return(close, 20)"
        prices: 价格数据 (index=date, columns=assets)
        factor_name: 因子名称 (可选)

    Returns:
        pd.Series: 因子值
    """
    try:
        result = evaluate_expression(factor_code, prices)
        if factor_name:
            result.name = factor_name
        return result
    except Exception as e:
        print(f"⚠️  因子计算失败 ({factor_code}): {e}")
        return pd.Series(dtype=float)


def compute_factors_batch(
    factor_exprs: list[dict],
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """批量计算因子。

    Args:
        factor_exprs: [{"factor_name": str, "factor_code": str}]
        prices: 价格数据

    Returns:
        pd.DataFrame: 因子值 (columns=factor_names)
    """
    results = {}
    for expr in factor_exprs:
        name = expr.get("factor_name", "unknown")
        code = expr.get("factor_code", "")
        if code:
            results[name] = compute_factor(code, prices, name)

    return pd.DataFrame(results)


def get_available_operators() -> list[str]:
    """获取可用算子列表。"""
    return list(OPERATORS.keys())
