"""
{strategy_name} 数据加载和评估。

此文件定义了策略的数据加载和评估接口。
框架通过 load_data() 和 evaluate() 与策略交互。

Agent 不应修改此文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

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

    自动从 DuckDB 加载价格数据。

    Returns:
        dict: 包含策略所需的所有数据
            - "prices": 价格数据 (DataFrame, index=date, columns=assets)
    """
    # 自动检测路径
    strategy_dir = Path(__file__).parent.resolve()
    # 向上查找包含 config.yaml 的目录作为 workspace
    workspace_dir = strategy_dir
    for _ in range(5):  # 最多向上查找 5 层
        if (workspace_dir / "config.yaml").exists():
            break
        workspace_dir = workspace_dir.parent
    else:
        # 如果找不到，使用默认路径
        workspace_dir = strategy_dir.parent.parent.parent

    strategy_name = strategy_dir.name

    # 尝试从 DuckDB 加载
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from strategy_research.core.db import load_price_data
        prices = load_price_data(workspace_dir, strategy_name)
        if not prices.empty:
            print(f"✓ 从 DuckDB 加载: {prices.shape[1]} 个资产, {prices.shape[0]} 个日期")
            return {"prices": prices}
    except Exception as e:
        print(f"⚠️  DuckDB 加载失败: {e}")

    # 尝试从 CSV 加载
    csv_path = strategy_dir / "data" / "prices.csv"
    if csv_path.exists():
        prices = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return {"prices": prices}

    print("❌ 未找到数据，请先导入数据")
    return {"prices": pd.DataFrame()}


def _generate_sample_data(n_assets: int = 10, n_days: int = 504) -> pd.DataFrame:
    """生成示例价格数据"""
    dates = pd.date_range("2020-01-01", periods=n_days, freq="D")
    assets = [f"asset_{i:03d}" for i in range(n_assets)]

    np.random.seed(42)
    returns = np.random.randn(n_days, n_assets) * 0.02
    prices = np.exp(np.cumsum(returns, axis=0))

    return pd.DataFrame(prices, index=dates, columns=assets)


# ============================================================
# 因子计算
# ============================================================
def compute_factors(prices: pd.DataFrame, factor_exprs: list[dict]) -> dict[str, pd.DataFrame]:
    """计算因子值。

    Args:
        prices: 价格数据 (index=date, columns=assets)
        factor_exprs: 因子表达式列表
            [{"factor_name": "mom_20d", "factor_code": "ts_return(close, 20)"}]

    Returns:
        dict: {factor_name: DataFrame} 每个因子的值 (index=date, columns=assets)
    """
    if not factor_exprs:
        return {}

    # 导入因子计算模块
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from strategy_research.core.compute_factor import compute_factor
    except ImportError:
        print("⚠️  无法导入 compute_factor，使用简单因子")
        return _compute_simple_factors(prices, factor_exprs)

    factors = {}
    for expr in factor_exprs:
        name = expr.get("factor_name", "unknown")
        code = expr.get("factor_code", "")
        if not code:
            continue

        try:
            # 对每个资产计算因子
            factor_values = {}
            for asset in prices.columns:
                asset_prices = prices[[asset]].copy()
                asset_prices.columns = ["close"]
                fv = compute_factor(code, asset_prices)
                if isinstance(fv, pd.Series):
                    factor_values[asset] = fv
                elif isinstance(fv, pd.DataFrame) and not fv.empty:
                    factor_values[asset] = fv.iloc[:, 0]

            if factor_values:
                factors[name] = pd.DataFrame(factor_values)
        except Exception as e:
            print(f"⚠️  因子 {name} 计算失败: {e}")

    return factors


def _compute_simple_factors(prices: pd.DataFrame, factor_exprs: list[dict]) -> dict[str, pd.DataFrame]:
    """简单因子计算 (不依赖 compute_factor 模块)"""
    factors = {}
    for expr in factor_exprs:
        name = expr.get("factor_name", "unknown")
        code = expr.get("factor_code", "")

        # 解析简单表达式
        if "ts_return" in code:
            # 提取窗口参数
            try:
                window = int(code.split(",")[-1].strip().rstrip(")"))
                factors[name] = prices.pct_change(window, fill_method=None)
            except (ValueError, IndexError):
                factors[name] = prices.pct_change(20, fill_method=None)
        elif "ts_std" in code:
            try:
                window = int(code.split(",")[-1].strip().rstrip(")"))
                factors[name] = prices.pct_change(fill_method=None).rolling(window).std()
            except (ValueError, IndexError):
                factors[name] = prices.pct_change(fill_method=None).rolling(20).std()
        else:
            # 默认: 20 日动量
            factors[name] = prices.pct_change(20, fill_method=None)

    return factors


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
            - "rebalance_freq": 调仓频率 (交易日)
        factor_exprs: 因子表达式列表 (FACTOR_EXPRS from strategy.py)
        factor_weight_method: 因子权重方式
            - "equal": 等权
            - "inv_vol": 逆波动率
        data: load_data() 返回的数据

    Returns:
        dict: 评估指标
    """
    prices = data.get("prices", pd.DataFrame())
    if prices.empty or len(prices) < 60:
        return _empty_metrics()

    # 参数
    top_n = params.get("top_n", 10)
    max_weight = params.get("max_weight", 0.25)
    rebalance_freq = params.get("rebalance_freq", 20)

    # 计算因子
    factors = compute_factors(prices, factor_exprs)
    if not factors:
        return _empty_metrics()

    # 计算综合分数
    scores = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for name, factor_df in factors.items():
        # 找到权重
        weight = 1.0
        for expr in factor_exprs:
            if expr.get("factor_name") == name:
                weight = expr.get("weight", 1.0)
                break
        # 对齐
        aligned = factor_df.reindex(prices.index).reindex(columns=prices.columns)
        scores = scores.add(aligned * weight, fill_value=0)

    # 计算日收益
    returns = prices.pct_change(fill_method=None)

    # 模拟回测
    nav = [1.0]
    prev_weights = {}
    weight_changes = []

    for i in range(rebalance_freq, len(prices)):
        date = prices.index[i]

        # 调仓日: 选股 + 计算权重
        if (i - rebalance_freq) % rebalance_freq == 0:
            day_scores = scores.iloc[i].dropna()
            if len(day_scores) < top_n:
                top_assets = day_scores.nlargest(min(top_n, len(day_scores))).index.tolist()
            else:
                top_assets = day_scores.nlargest(top_n).index.tolist()

            # 计算权重
            if factor_weight_method == "inv_vol" and len(top_assets) > 0:
                lookback = min(60, i)
                vols = returns.iloc[i - lookback:i][top_assets].std()
                vols = vols.clip(lower=0.01)
                inv_vol = 1.0 / vols
                new_weights = (inv_vol / inv_vol.sum()).to_dict()
            else:
                new_weights = {a: 1.0 / len(top_assets) for a in top_assets}

            # 最大权重约束
            for a in new_weights:
                if new_weights[a] > max_weight:
                    new_weights[a] = max_weight
            total = sum(new_weights.values())
            if total > 0:
                new_weights = {a: w / total for a, w in new_weights.items()}

            # 记录换手
            all_assets = set(list(prev_weights.keys()) + list(new_weights.keys()))
            change = sum(abs(new_weights.get(a, 0) - prev_weights.get(a, 0)) for a in all_assets) / 2
            weight_changes.append(change)

            prev_weights = new_weights

        # 计算日收益
        daily_ret = 0.0
        for asset, w in prev_weights.items():
            if asset in returns.columns:
                r = returns.iloc[i][asset]
                if pd.notna(r):
                    daily_ret += w * r

        nav.append(nav[-1] * (1 + daily_ret))

    # 构造 NAV 序列
    nav_series = pd.Series(nav, index=prices.index[rebalance_freq - 1:rebalance_freq - 1 + len(nav)])

    # 计算指标
    ann_return = _ann_return(nav_series)
    max_dd = _max_drawdown(nav_series)
    sharpe = _sharpe(nav_series)
    ann_vol = _ann_vol(nav_series)
    sortino = _sortino(nav_series)
    turnover = np.mean(weight_changes) * 252 if weight_changes else 0.0

    # Calmar
    calmar = ann_return / abs(max_dd) if max_dd < 0 else 0.0

    return {
        GOAL_METRIC: calmar,
        "calmar": calmar,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sortino": sortino,
        "turnover": turnover,
        "trades": len(weight_changes),
    }


def _empty_metrics() -> dict:
    """返回空指标"""
    return {
        GOAL_METRIC: 0.0,
        "calmar": 0.0,
        "sharpe": 0.0,
        "max_dd": 0.0,
        "ann_return": 0.0,
        "ann_vol": 0.0,
        "sortino": 0.0,
        "turnover": 0.0,
        "trades": 0,
    }


# ============================================================
# 辅助函数
# ============================================================
def _ann_return(nav: pd.Series, freq: int = 252) -> float:
    """计算年化收益率。"""
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
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
    rets = nav.pct_change(fill_method=None).dropna()
    return float(rets.std() * np.sqrt(freq)) if not rets.empty else 0.0


def _sortino(nav: pd.Series, freq: int = 252) -> float:
    """计算 Sortino 比率。"""
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
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
