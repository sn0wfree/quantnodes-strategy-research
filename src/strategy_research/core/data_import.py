"""数据导入工具。

支持从多种数据源导入价格数据到 DuckDB:
- CSV/Parquet (本地文件)
- Tushare (A 股/ETF/指数/港股)
- iFinD (宏观/港美股)
- FRED (美国宏观 56 系列)
- AKShare (免费全市场)
- 示例数据 (合成)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .db import (
    save_price_data,
    save_ohlcv_data,
    get_price_data_info,
    compute_data_fingerprint,
    update_data_fingerprint,
    get_last_import_date,
    update_import_meta,
)


# ============================================================
# API 数据源导入
# ============================================================

def import_from_source(
    workspace_path: Path,
    strategy_name: str,
    source: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool = True,
) -> bool:
    """从指定数据源导入数据。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        source: 数据源名称 (tushare/ifind/fred/akshare/auto)
        codes: 资产代码列表
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        incremental: 是否增量更新

    Returns:
        bool: 是否成功
    """
    from .data_source import resolve_loader, detect_market, NoAvailableSourceError

    # 自动检测市场
    if source == "auto":
        # 按市场分组
        market_groups = {}
        for code in codes:
            market = detect_market(code)
            if market not in market_groups:
                market_groups[market] = []
            market_groups[market].append(code)

        # 按市场分别获取
        all_success = True
        for market, market_codes in market_groups.items():
            try:
                loader = resolve_loader(market)
                success = _import_with_loader(
                    workspace_path, strategy_name, loader,
                    market_codes, start_date, end_date, incremental
                )
                if not success:
                    all_success = False
            except NoAvailableSourceError as e:
                print(f"❌ 市场 '{market}' 没有可用数据源: {e}")
                all_success = False

        return all_success
    else:
        # 指定数据源
        try:
            loader = resolve_loader(detect_market(codes[0]) if codes else "a_share")
            # 如果指定了 source，尝试使用指定的
            from .data_source import get_loader_or_fallback
            loader_cls = get_loader_or_fallback(source)
            loader = loader_cls()
            return _import_with_loader(
                workspace_path, strategy_name, loader,
                codes, start_date, end_date, incremental
            )
        except NoAvailableSourceError as e:
            print(f"❌ 数据源 '{source}' 不可用: {e}")
            return False


def _import_with_loader(
    workspace_path: Path,
    strategy_name: str,
    loader,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool,
) -> bool:
    """使用指定 loader 导入数据"""
    # 增量更新: 调整 start_date
    if incremental:
        actual_codes = []
        for code in codes:
            last = get_last_import_date(workspace_path, strategy_name, code)
            if last:
                # 从最后日期的下一天开始
                next_day = (last + timedelta(days=1)).strftime("%Y-%m-%d")
                if next_day > end_date:
                    print(f"  ⏭  {code} 已是最新 (最后更新: {last})")
                    continue
                actual_codes.append((code, next_day))
            else:
                actual_codes.append((code, start_date))

        if not actual_codes:
            print("✓ 所有数据已是最新")
            return True

        codes_to_fetch = [c for c, _ in actual_codes]
        fetch_start = min(s for _, s in actual_codes)
    else:
        codes_to_fetch = codes
        fetch_start = start_date

    print(f"📡 从 {loader.name} 获取 {len(codes_to_fetch)} 个资产...")

    # 获取数据
    data_map = loader.fetch(codes_to_fetch, fetch_start, end_date)

    if not data_map:
        print(f"❌ {loader.name} 未返回数据")
        return False

    # 保存到 DuckDB
    success_count = 0
    for code, df in data_map.items():
        if df is None or df.empty:
            continue

        # 保留完整 OHLCV (修复：之前 df[["close"]] 把 OHLCV 全丢)
        ok = save_ohlcv_data(workspace_path, strategy_name, code, df)
        if ok:
            success_count += 1
            # 更新导入元数据
            update_import_meta(workspace_path, strategy_name, code, end_date)

    print(f"✓ 导入完成: {success_count}/{len(codes_to_fetch)} 个资产")

    # 显示信息
    info = get_price_data_info(workspace_path, strategy_name)
    if info:
        print(f"   总计: {info.get('n_assets', 0)} 个资产, "
              f"{info.get('n_dates', 0)} 个日期")

    return success_count > 0


# ============================================================
# Tushare 专用导入
# ============================================================

def import_tushare(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    token: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 Tushare 导入价格数据。"""
    from .data_source.tushare_loader import TushareLoader

    loader = TushareLoader(token=token, workspace_path=workspace_path)
    if not loader.is_available():
        print("❌ Tushare 不可用: 请设置 TUSHARE_TOKEN")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# iFinD 专用导入
# ============================================================

def import_ifind(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    token: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 iFinD 导入数据。"""
    from .data_source.ifind_loader import IFindLoader

    loader = IFindLoader(token=token, workspace_path=workspace_path)
    if not loader.is_available():
        print("❌ iFinD 不可用: 请设置 IFIND_MCP_TOKEN")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# FRED 专用导入
# ============================================================

def import_fred(
    workspace_path: Path,
    strategy_name: str,
    series_ids: list[str],
    start_date: str,
    end_date: str,
    api_key: Optional[str] = None,
    incremental: bool = True,
) -> bool:
    """从 FRED 导入宏观数据。"""
    from .data_source.fred_loader import FredLoader

    loader = FredLoader(api_key=api_key, workspace_path=workspace_path)
    if not loader.is_available():
        print("❌ FRED 不可用: 请设置 FRED_API_KEY")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        series_ids, start_date, end_date, incremental
    )


# ============================================================
# AKShare 专用导入
# ============================================================

def import_akshare(
    workspace_path: Path,
    strategy_name: str,
    codes: list[str],
    start_date: str,
    end_date: str,
    incremental: bool = True,
) -> bool:
    """从 AKShare 导入数据 (免费)。"""
    from .data_source.akshare_loader import AKShareLoader

    loader = AKShareLoader()
    if not loader.is_available():
        print("❌ AKShare 不可用: 请安装 akshare (pip install akshare)")
        return False

    return _import_with_loader(
        workspace_path, strategy_name, loader,
        codes, start_date, end_date, incremental
    )


# ============================================================
# 本地文件导入 (保持兼容)
# ============================================================

def import_csv(
    workspace_path: Path,
    strategy_name: str,
    csv_path: str,
    date_column: str = "date",
    price_column: str = "close",
    asset_column: str | None = None,
) -> bool:
    """从 CSV 导入价格数据。"""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        print(f"❌ CSV 文件不存在: {csv_path}")
        return False

    try:
        df = pd.read_csv(csv_path, parse_dates=[date_column])

        if asset_column:
            prices = df.pivot(index=date_column, columns=asset_column, values=price_column)
        else:
            prices = df.set_index(date_column)

        success = save_price_data(workspace_path, strategy_name, prices)

        if success:
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
            if "close" in df.columns:
                prices = df["close"].unstack()
            else:
                num_cols = df.select_dtypes(include=["number"]).columns
                if len(num_cols) > 0:
                    prices = df[num_cols[0]].unstack()
                else:
                    print("❌ 无法找到价格列")
                    return False
        else:
            prices = df

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
    """生成示例价格数据 (宽面板，仅 close 列)。

    Returns:
        pd.DataFrame: (T, N) 面板, index=date, columns=assets, 值为 close
    """
    import numpy as np

    dates = pd.date_range(start_date, periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    prices = np.exp(np.cumsum(returns, axis=0))

    return pd.DataFrame(prices, index=dates, columns=assets)


def generate_sample_ohlcv_data(
    n_assets: int = 10,
    n_days: int = 504,
    start_date: str = "2020-01-01",
) -> dict[str, "pd.DataFrame"]:
    """生成示例 OHLCV 数据 (dict[code → DataFrame])，模拟 loader.fetch() 返回格式。

    用于测试 save_ohlcv_data 与 OHLCV 完整性检查。
    """
    import numpy as np

    dates = pd.date_range(start_date, periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    close = np.exp(np.cumsum(returns, axis=0))

    intraday = np.abs(np.random.randn(n_days, n_assets)) * 0.005  # 0.5% 噪声
    high = close * (1 + intraday)
    low = close * (1 - intraday)
    open_ = close * (1 + np.random.randn(n_days, n_assets) * 0.002)
    volume = np.random.randint(100_000, 10_000_000, size=(n_days, n_assets)).astype(float)

    result = {}
    for i, asset in enumerate(assets):
        df = pd.DataFrame({
            "open": open_[:, i],
            "high": high[:, i],
            "low": low[:, i],
            "close": close[:, i],
            "volume": volume[:, i],
        }, index=dates)
        df.index.name = "date"
        result[asset] = df

    return result
