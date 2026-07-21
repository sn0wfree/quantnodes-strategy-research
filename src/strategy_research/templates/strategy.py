"""
{strategy_name} 策略配置。
Agent 可以修改: PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD
"""

# ============================================================
# 策略参数 (Agent 可改)
# ============================================================
PARAMS = {
    "top_n": 5,                    # 选择资产数
    "max_weight": 0.25,            # 单资产最大权重
    "rebalance_freq": 20,          # 调仓频率 (交易日)
}

# ============================================================
# 因子表达式 (Agent 可改)
# ============================================================
FACTOR_EXPRS = [
    {"factor_name": "momentum_20d", "factor_code": "ts_return(close, 20)", "weight": 0.5, "category": "momentum"},
    {"factor_name": "vol_20d", "factor_code": "ts_std(ts_return(close, 1), 20)", "weight": -0.3, "category": "volatility"},
    {"factor_name": "reversal_5d", "factor_code": "ts_return(close, 5)", "weight": 0.2, "category": "reversal"},
]

# ============================================================
# 因子权重方式 (Agent 可改)
# ============================================================
FACTOR_WEIGHT_METHOD = "inv_vol"  # "equal" | "inv_vol"

# ============================================================
# 以下不改
# ============================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path
    # 将当前目录添加到 sys.path 以便导入 prepare
    sys.path.insert(0, str(Path(__file__).parent))
    import prepare
    data = prepare.load_data()
    metrics = prepare.evaluate(PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD, data)
    print("---")
    for k, v in metrics.items():
        print(f"{k}: {v:.6f}")
