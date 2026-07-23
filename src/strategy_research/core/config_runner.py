"""YAML 配置驱动回测。

从 YAML 文件加载配置，创建策略和引擎，运行回测。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .db import load_price_data
from .utils.backtest_config import (
    BacktestConfig,
    CostConfig,
    StopLossConfig,
    TrendFilterConfig,
    VolTargetingConfig,
)
from .utils.strategy_engine import BacktestResult, StrategyEngine

# ============================================================
# 1. YAML 加载
# ============================================================

def load_yaml_config(path: str | Path) -> dict:
    """加载 YAML 配置文件."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# 2. 配置创建
# ============================================================

def create_cost_config(cfg: dict) -> CostConfig:
    """从 YAML 创建 CostConfig."""
    cost_cfg = cfg.get("cost", {})
    return CostConfig(
        enabled=cost_cfg.get("enabled", False),
        commission_bp=cost_cfg.get("commission_bp", 5),
        slippage_bp=cost_cfg.get("slippage_bp", 10),
        impact_factor=cost_cfg.get("impact_factor", 0.1),
        flat_cost_bps=cost_cfg.get("flat_cost_bps"),
    )


def create_engine(cfg: dict) -> StrategyEngine:
    """从 YAML 配置创建 StrategyEngine."""
    risk_cfg = cfg.get("risk", {})
    vt_cfg = risk_cfg.get("vol_targeting", {})
    tf_cfg = risk_cfg.get("trend_filter", {})
    sl_cfg = risk_cfg.get("stop_loss", {})

    vt = None
    if vt_cfg.get("enabled"):
        vt = VolTargetingConfig(
            enabled=True,
            target_vol=vt_cfg.get("target_vol", 0.15),
            lookback=vt_cfg.get("lookback", 60),
            min_scale=vt_cfg.get("min_scale", 0.3),
            max_scale=vt_cfg.get("max_scale", 2.0),
        )

    tf = None
    if tf_cfg.get("enabled"):
        tf = TrendFilterConfig(
            enabled=True,
            ma_window=tf_cfg.get("ma_window", 200),
            bear_exposure=tf_cfg.get("bear_exposure", 0.5),
        )

    sl = None
    if sl_cfg.get("enabled"):
        sl = StopLossConfig(
            enabled=True,
            threshold=sl_cfg.get("threshold", -0.10),
            cooldown_weeks=sl_cfg.get("cooldown_weeks", 5),
        )

    return StrategyEngine(vol_targeting=vt, trend_filter=tf, stop_loss=sl)


def create_backtest_config(cfg: dict) -> BacktestConfig:
    """从 YAML 创建 BacktestConfig."""
    rebal_cfg = cfg.get("rebalance", {})
    return BacktestConfig(
        rebal_freq=rebal_cfg.get("freq", "M"),
        min_history=rebal_cfg.get("min_history", 252),
        top_n=cfg.get("top_n", 10),
        max_weight=cfg.get("max_weight", 0.25),
        weight_method=cfg.get("weight_method", "inverse_vol"),
        cost=create_cost_config(cfg),
    )


# ============================================================
# 3. 数据加载
# ============================================================

def load_data(cfg: dict, workspace_path: Path) -> pd.DataFrame:
    """从 DuckDB 加载数据."""
    strategy_name = cfg.get("strategy", {}).get("name", "default")
    data_cfg = cfg.get("data", {})

    source = data_cfg.get("source", "duckdb")

    if source == "duckdb":
        return load_price_data(
            workspace_path,
            strategy_name,
            start_date=data_cfg.get("start_date"),
            end_date=data_cfg.get("end_date"),
        )
    else:
        raise ValueError(f"不支持的数据源: {source}")


# ============================================================
# 4. 策略创建
# ============================================================

def create_strategy(cfg: dict):
    """从 YAML 配置创建策略实例."""
    factors = cfg.get("factors", [])
    params = cfg.get("strategy_params", {})

    # 合并顶层参数到 params
    for key in ["top_n", "max_weight", "weight_method", "vol_lookback"]:
        if key in cfg and key not in params:
            params[key] = cfg[key]

    return FactorStrategy(factors, params)


class FactorStrategy:
    """因子策略: 基于因子表达式计算权重.

    支持 3 种因子类型（按 YAML 配置自动识别）：
    1. **code**: 表达式因子（如 `ts_return(close, 20)`）
    2. **alpha_id**: 单个 Alpha Zoo 因子（如 `gtja191_001`）
    3. **alpha_ids**: 多个 Alpha Zoo 因子组合 + combination 方法
    """

    def __init__(self, factors: list[dict], params: dict):
        self.factors = factors
        self.params = params

    def compute_weights(
        self,
        date: pd.Timestamp,
        price_panel: pd.DataFrame,
        nav_history: pd.Series,
    ) -> dict[str, float]:
        """计算权重."""
        from .alpha_zoo_adapter import AlphaZooAdapter
        from .compute_factor import compute_factor

        # 1. 计算因子值
        factor_values = {}
        alpha_zoo = None  # lazy init

        for factor in self.factors:
            name = factor.get("name", "unknown")

            # 方式 1: 表达式因子
            code = factor.get("code", "")
            if code:
                try:
                    factor_values[name] = compute_factor(code, price_panel.loc[:date])
                except Exception as e:
                    print(f"⚠️  因子 {name} 计算失败: {e}")
                continue

            # 方式 2: 单个 Alpha Zoo 因子
            alpha_id = factor.get("alpha_id", "")
            if alpha_id:
                if alpha_zoo is None:
                    alpha_zoo = AlphaZooAdapter()
                try:
                    wide = alpha_zoo.compute_as_wide(alpha_id, price_panel.loc[:date])
                    factor_values[name] = wide
                except Exception as e:
                    print(f"⚠️  Alpha Zoo {alpha_id} 计算失败: {e}")
                continue

            # 方式 3: 多个 Alpha Zoo 因子组合
            alpha_ids = factor.get("alpha_ids", [])
            if alpha_ids:
                if alpha_zoo is None:
                    alpha_zoo = AlphaZooAdapter()
                combination = factor.get("combination", "equal")
                try:
                    # 计算多个 Alpha，然后按方法组合
                    df_batch = alpha_zoo.compute_batch(alpha_ids, price_panel.loc[:date])
                    if df_batch.empty:
                        continue
                    if combination == "equal":
                        combined = df_batch.mean(axis=1)
                    elif combination == "ic_weighted":
                        # 简单等权（IC 加权需另算权重）
                        combined = df_batch.mean(axis=1)
                    else:
                        combined = df_batch.mean(axis=1)
                    # 转为 wide 形式（index=date, columns=assets）
                    combined_wide = combined.unstack()
                    factor_values[name] = combined_wide
                except Exception as e:
                    print(f"⚠️  Alpha Zoo 组合 {alpha_ids} 计算失败: {e}")

        # 2. 计算综合分数 — 取每个因子在当前 date 的横截面值
        scores = pd.Series(0.0, index=price_panel.columns)
        for factor in self.factors:
            name = factor.get("name", "unknown")
            weight = factor.get("weight", 1.0)
            if name in factor_values:
                fv = factor_values[name]
                # 取当前 date 的横截面（per-asset）
                if isinstance(fv, pd.DataFrame):
                    # wide DataFrame (T,N) → 取当前 date 一行
                    if date in fv.index:
                        current = fv.loc[date]
                    else:
                        # date 不在索引里（如 expression 因子返回空 Series），fallback 到最后一行
                        current = fv.iloc[-1] if len(fv) > 0 else pd.Series(0.0, index=price_panel.columns)
                else:
                    # Series（单资产情况）
                    current = fv
                # 对齐到 price_panel 的列
                aligned = current.reindex(price_panel.columns, fill_value=0)
                scores = scores.add(aligned * weight, fill_value=0)

        # 3. 选择 top_n
        top_n = self.params.get("top_n", 10)
        selected = scores.nlargest(top_n).index.tolist()

        # 4. 计算权重
        weight_method = self.params.get("weight_method", "inverse_vol")
        if weight_method == "inverse_vol":
            lookback = self.params.get("vol_lookback", 60)
            vols = price_panel[selected].pct_change(fill_method=None).iloc[-lookback:].std()
            inv_vol = 1.0 / vols.clip(lower=0.01)
            weights = (inv_vol / inv_vol.sum()).to_dict()
        else:
            # 等权
            weights = {c: 1.0 / len(selected) for c in selected}

        return weights

    def on_risk_check(
        self,
        weights: dict[str, float],
        nav_history: pd.Series,
        date: pd.Timestamp,
    ) -> dict[str, float]:
        """自定义风控."""
        from .backtest_utils import apply_max_weight, normalize_weights

        max_weight = self.params.get("max_weight", 0.25)
        weights = apply_max_weight(weights, max_weight)
        return normalize_weights(weights)


# ============================================================
# 5. 一键回测
# ============================================================

def run_from_yaml(yaml_path: str | Path, workspace_path: Path) -> BacktestResult:
    """YAML → Strategy → Engine → BacktestResult.

    Args:
        yaml_path: YAML 配置文件路径
        workspace_path: 工作区路径 (用于 DuckDB)

    Returns:
        BacktestResult (nav_daily, weights_history, metrics)
    """
    cfg = load_yaml_config(yaml_path)
    strategy = create_strategy(cfg)
    engine = create_engine(cfg)
    data = load_data(cfg, workspace_path)

    if data.empty:
        raise ValueError("数据为空，请先导入价格数据")

    # 归一化价格
    data_norm = data / data.iloc[0]

    # 参数
    rebal_cfg = cfg.get("rebalance", {})
    rebal_freq = rebal_cfg.get("freq", "M")
    min_history = rebal_cfg.get("min_history", 252)
    cost = create_cost_config(cfg)

    return engine.run(
        price_panel=data_norm,
        strategy=strategy,
        rebal_freq=rebal_freq,
        min_history=min_history,
        cost=cost,
    )
