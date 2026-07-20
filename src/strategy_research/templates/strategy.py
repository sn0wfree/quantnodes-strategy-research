"""
{strategy_name} 策略配置。
Agent 可以修改: PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD
"""

# ============================================================
# 策略参数 (Agent 可改)
# ============================================================
PARAMS = {{
    # TODO: 添加策略参数
}}

# ============================================================
# 因子表达式 (Agent 可改)
# ============================================================
FACTOR_EXPRS = [
    # 示例:
    # {{"factor_name": "momentum_20d", "factor_code": "ts_return(close, 20)", "category": "momentum"}},
]

# ============================================================
# 因子权重方式 (Agent 可改)
# ============================================================
FACTOR_WEIGHT_METHOD = "inv_vol"  # "equal" | "inv_vol" | "ic_ir" | "risk_parity"

# ============================================================
# 以下不改
# ============================================================
if __name__ == "__main__":
    import importlib
    prepare = importlib.import_module(".prepare", package=__package__)
    data = prepare.load_data()
    metrics = prepare.evaluate(PARAMS, FACTOR_EXPRS, FACTOR_WEIGHT_METHOD, data)
    print("---")
    for k, v in metrics.items():
        print(f"{{k}}: {{v:.6f}}")
