"""数据导入工具。

支持从 CSV/Parquet 导入价格数据到 DuckDB。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .db import save_price_data, get_price_data_info, compute_data_fingerprint, update_data_fingerprint


def import_csv(
    workspace_path: Path,
    strategy_name: str,
    csv_path: str,
    date_column: str = "date",
    price_column: str = "close",
    asset_column: str | None = None,
) -> bool:
    """从 CSV 导入价格数据。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        csv_path: CSV 文件路径
        date_column: 日期列名
        price_column: 价格列名
        asset_column: 资产代码列名 (None 表示宽格式)

    Returns:
        bool: 是否成功
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"❌ CSV 文件不存在: {csv_path}")
        return False

    try:
        df = pd.read_csv(csv_path, parse_dates=[date_column])

        if asset_column:
            # 长格式: date, asset, close
            prices = df.pivot(index=date_column, columns=asset_column, values=price_column)
        else:
            # 宽格式: date, asset1, asset2, ...
            prices = df.set_index(date_column)

        # 保存到 DuckDB
        success = save_price_data(workspace_path, strategy_name, prices)

        if success:
            # 更新数据指纹
            fingerprint = compute_data_fingerprint(prices)
            update_data_fingerprint(
                workspace_path, strategy_name, "price_data",
                fingerprint, len(prices)
            )

            info = get_price_data_info(workspace_path, strategy_name)
            print(f"✓ 导入 CSV: {info.get('n_assets', 0)} 个资产, "
                  f"{info.get('n_dates', 0)} 个日期")

        return success

    except Exception as e:
        print(f"❌ 导入 CSV 失败: {e}")
        return False


def import_parquet(
    workspace_path: Path,
    strategy_name: str,
    parquet_path: str,
) -> bool:
    """从 Parquet 导入价格数据。"""
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        print(f"❌ Parquet 文件不存在: {parquet_path}")
        return False

    try:
        df = pd.read_parquet(parquet_path)

        if isinstance(df.index, pd.MultiIndex):
            # MultiIndex: (date, asset)
            if "close" in df.columns:
                prices = df["close"].unstack()
            else:
                # 尝试第一个数值列
                num_cols = df.select_dtypes(include=["number"]).columns
                if len(num_cols) > 0:
                    prices = df[num_cols[0]].unstack()
                else:
                    print("❌ 无法找到价格列")
                    return False
        else:
            # 宽格式
            prices = df

        # 保存到 DuckDB
        success = save_price_data(workspace_path, strategy_name, prices)

        if success:
            fingerprint = compute_data_fingerprint(prices)
            update_data_fingerprint(
                workspace_path, strategy_name, "price_data",
                fingerprint, len(prices)
            )

            info = get_price_data_info(workspace_path, strategy_name)
            print(f"✓ 导入 Parquet: {info.get('n_assets', 0)} 个资产, "
                  f"{info.get('n_dates', 0)} 个日期")

        return success

    except Exception as e:
        print(f"❌ 导入 Parquet 失败: {e}")
        return False


def import_dataframe(
    workspace_path: Path,
    strategy_name: str,
    prices: pd.DataFrame,
) -> bool:
    """从 DataFrame 导入价格数据。"""
    try:
        success = save_price_data(workspace_path, strategy_name, prices)

        if success:
            fingerprint = compute_data_fingerprint(prices)
            update_data_fingerprint(
                workspace_path, strategy_name, "price_data",
                fingerprint, len(prices)
            )

            info = get_price_data_info(workspace_path, strategy_name)
            print(f"✓ 导入 DataFrame: {info.get('n_assets', 0)} 个资产, "
                  f"{info.get('n_dates', 0)} 个日期")

        return success

    except Exception as e:
        print(f"❌ 导入 DataFrame 失败: {e}")
        return False


def generate_sample_data(
    n_assets: int = 10,
    n_days: int = 504,
    start_date: str = "2020-01-01",
) -> pd.DataFrame:
    """生成示例价格数据。"""
    import numpy as np

    dates = pd.date_range(start_date, periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    # 生成随机价格 (几何布朗运动)
    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    prices = np.exp(np.cumsum(returns, axis=0))

    return pd.DataFrame(prices, index=dates, columns=assets)
