"""回测执行工具。"""
from __future__ import annotations


def run_backtest(strategy_path: str) -> dict:
    """运行策略回测。
    
    Returns:
        dict: {"calmar": float, "sharpe": float, ...}
    """
    # TODO: 实现回测执行
    raise NotImplementedError("请实现 run_backtest()")
