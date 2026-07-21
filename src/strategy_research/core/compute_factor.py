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
    return series.pct_change(window)


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
    return series.pct_change(periods)


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
    """安全除法: a / (b + eps * sign(b)), 除零 → NaN。"""
    return a / (b + eps * np.sign(b))


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
}


# ============================================================
# 表达式解析
# ============================================================

def parse_expression(expr: str) -> tuple[str, list]:
    """解析因子表达式。

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

    Args:
        expr: 因子表达式，如 "ts_return(close, 20)"
        data: 价格数据 (index=date, columns=assets 或 columns 包含因子所需列)

    Returns:
        pd.Series: 因子值
    """
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
