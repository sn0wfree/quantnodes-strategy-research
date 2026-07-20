"""因子计算工具。

支持时序算子和截面算子的因子值计算。
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
# 算子注册表
# ============================================================

OPERATORS = {
    # 时序算子
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
    # 截面算子
    "rank": rank,
    "zscore": zscore,
    "scale": scale,
    "winsorize": winsorize,
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
