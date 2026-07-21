"""
{strategy_name} 策略配置。
Agent 可以修改: PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD
"""

# ============================================================
# 策略参数 (Agent 可改)
# ============================================================
PARAMS = {
    "top_n": 1,                    # 只持有 1 个资产 (HS300 指数)
    "max_weight": 1.0,             # 全仓
    "rebalance_freq": 999999,      # 不调仓 (buy and hold)
}

# ============================================================
# 因子表达式 (Agent 可改)
# ============================================================
FACTOR_EXPRS = [
    # 无因子 — 纯 buy and hold HS300
    # Agent 从这里开始迭代
]

# ============================================================
# 因子权重方式 (Agent 可改)
# ============================================================
FACTOR_WEIGHT_METHOD = "equal"  # "equal" | "inv_vol"

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
