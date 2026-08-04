"""数据清洗工具集

基于业界量化金融数据清洗标准，提供参数驱动的预设模式设计。

支持的预设模式：
- quick: 快速清洗（只去重）
- standard: 标准清洗（去重 + 填充缺失值）
- thorough: 彻底清洗（去重 + 填充 + 异常值检测 + 收益率）
- custom: 自定义清洗（完全由 params 控制）
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import pandas as pd
import numpy as np

# 预设模式定义
PRESETS = {
    "quick": {
        "description": "快速清洗：只去重",
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "none",
            "outlier_method": "none",
            "add_returns": False,
        }
    },
    "standard": {
        "description": "标准清洗：去重 + 填充缺失值",
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "ffill",
            "impute_columns": ["open", "high", "low", "close"],
            "impute_limit": 3,
            "outlier_method": "none",
            "add_returns": False,
        }
    },
    "thorough": {
        "description": "彻底清洗：去重 + 填充 + 异常值检测",
        "params": {
            "dedup_strategy": "first",
            "dedup_subset": ["asset", "date"],
            "impute_method": "ffill",
            "impute_columns": ["open", "high", "low", "close", "volume"],
            "impute_limit": 5,
            "outlier_method": "iqr",
            "outlier_threshold": 1.5,
            "outlier_action": "flag",
            "add_returns": True,
        }
    },
    "custom": {
        "description": "自定义清洗：完全由 params 控制",
        "params": {}
    }
}


@dataclass
class CleaningReport:
    """清洗报告"""
    initial_rows: int = 0
    final_rows: int = 0
    duplicates_removed: int = 0
    missing_filled: int = 0
    outliers_detected: int = 0
    params_applied: dict = field(default_factory=dict)
    message: str = ""


def clean_data(
    df: pd.DataFrame,
    preset: str = "standard",
    params: Optional[dict] = None,
    dry_run: bool = True
) -> tuple[pd.DataFrame, CleaningReport]:
    """执行数据清洗

    Args:
        df: 输入数据
        preset: 预设模式 (quick/standard/thorough/custom)
        params: 自定义参数 (preset=custom 时生效)
        dry_run: 是否只生成报告不执行

    Returns:
        tuple: (清洗后的数据, 清洗报告)
    """
    # 合并参数
    if preset == "custom":
        clean_params = params or {}
    else:
        clean_params = PRESETS.get(preset, PRESETS["standard"])["params"].copy()
        if params:
            clean_params.update(params)

    # 创建报告
    report = CleaningReport(
        initial_rows=len(df),
        final_rows=len(df),
        duplicates_removed=0,
        missing_filled=0,
        outliers_detected=0,
        params_applied=clean_params,
        message=""
    )

    if dry_run:
        # dry_run 模式：只计算报告，不修改数据
        result_df = df.copy()
    else:
        result_df = df.copy()

    # 1. 去重
    dedup_strategy = clean_params.get("dedup_strategy")
    if dedup_strategy and dedup_strategy != "none":
        result_df, removed = _dedup(result_df, clean_params)
        report.duplicates_removed = removed

    # 2. 填充缺失值
    impute_method = clean_params.get("impute_method")
    if impute_method and impute_method != "none":
        result_df, filled = _impute(result_df, clean_params)
        report.missing_filled = filled

    # 3. 异常值检测
    outlier_method = clean_params.get("outlier_method")
    if outlier_method and outlier_method != "none":
        result_df, detected = _detect_outliers(result_df, clean_params)
        report.outliers_detected = detected

    # 4. 添加收益率
    if clean_params.get("add_returns"):
        result_df = _add_returns(result_df)

    report.final_rows = len(result_df)
    report.message = _build_message(report)

    return result_df, report


def _dedup(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """去重

    Args:
        df: 输入数据
        params: 清洗参数

    Returns:
        tuple: (去重后的数据, 删除的行数)
    """
    strategy = params.get("dedup_strategy", "first")
    subset = params.get("dedup_subset", ["asset", "date"])

    before = len(df)

    # 检查 subset 列是否存在
    valid_subset = [col for col in subset if col in df.columns]
    if not valid_subset:
        return df, 0

    if strategy == "first":
        df = df.drop_duplicates(subset=valid_subset, keep='first')
    elif strategy == "last":
        df = df.drop_duplicates(subset=valid_subset, keep='last')
    elif strategy == "max_volume":
        if 'volume' in df.columns:
            idx = df.groupby(valid_subset)['volume'].idxmax()
            df = df.loc[idx]

    return df, before - len(df)


def _impute(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """填充缺失值

    Args:
        df: 输入数据
        params: 清洗参数

    Returns:
        tuple: (填充后的数据, 填充的数量)
    """
    method = params.get("impute_method", "ffill")
    columns = params.get("impute_columns", ["open", "high", "low", "close"])
    limit = params.get("impute_limit")

    # 检查列是否存在
    valid_columns = [col for col in columns if col in df.columns]
    if not valid_columns:
        return df, 0

    before = int(df[valid_columns].isna().sum().sum())

    if method == "ffill":
        df[valid_columns] = df[valid_columns].ffill(limit=limit)
    elif method == "bfill":
        df[valid_columns] = df[valid_columns].bfill(limit=limit)
    elif method == "interpolate":
        df[valid_columns] = df[valid_columns].interpolate(method='linear', limit=limit)
    elif method == "zero":
        df[valid_columns] = df[valid_columns].fillna(0)

    after = int(df[valid_columns].isna().sum().sum())
    return df, before - after


def _detect_outliers(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, int]:
    """检测异常值

    Args:
        df: 输入数据
        params: 清洗参数

    Returns:
        tuple: (处理后的数据, 异常值数量)
    """
    method = params.get("outlier_method", "iqr")
    threshold = params.get("outlier_threshold", 1.5)
    action = params.get("outlier_action", "flag")

    if 'close' not in df.columns:
        return df, 0

    if method == "iqr":
        Q1 = df['close'].quantile(0.25)
        Q3 = df['close'].quantile(0.75)
        IQR = Q3 - Q1
        lower = Q1 - threshold * IQR
        upper = Q3 + threshold * IQR
        outlier_mask = (df['close'] < lower) | (df['close'] > upper)
    elif method == "zscore":
        try:
            from scipy import stats
            z_scores = stats.zscore(df['close'].dropna())
            outlier_mask = pd.Series(False, index=df.index)
            outlier_mask[df['close'].dropna().index] = abs(z_scores) > threshold
        except ImportError:
            return df, 0
    else:
        return df, 0

    outlier_count = int(outlier_mask.sum())

    if action == "flag":
        df['is_outlier'] = outlier_mask
    elif action == "remove":
        df = df[~outlier_mask]
    elif action == "clip":
        if method == "iqr":
            df['close'] = df['close'].clip(lower=lower, upper=upper)

    return df, outlier_count


def _add_returns(df: pd.DataFrame) -> pd.DataFrame:
    """添加收益率

    Args:
        df: 输入数据

    Returns:
        pd.DataFrame: 添加收益率后的数据
    """
    if 'close' in df.columns:
        df['return'] = df['close'].pct_change()
    return df


def _build_message(report: CleaningReport) -> str:
    """构建清洗消息

    Args:
        report: 清洗报告

    Returns:
        str: 清洗消息
    """
    parts = []
    if report.duplicates_removed > 0:
        parts.append(f"删除 {report.duplicates_removed} 条重复数据")
    if report.missing_filled > 0:
        parts.append(f"填充 {report.missing_filled} 条缺失值")
    if report.outliers_detected > 0:
        parts.append(f"检测到 {report.outliers_detected} 条异常值")

    if not parts:
        return "数据清洗完成，无需处理"

    return "清洗完成：" + "，".join(parts)
