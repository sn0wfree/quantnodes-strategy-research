"""Artifacts — 写回测产物到 run_dir (OHLCV/equity/trades CSV)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .models import TradeRecord


def write_equity_curve(
    run_dir: Path,
    equity_snapshots: list,
) -> None:
    """写 equity_curve.csv。"""
    if not equity_snapshots:
        return

    data = {
        "timestamp": [s.timestamp for s in equity_snapshots],
        "capital": [s.capital for s in equity_snapshots],
        "unrealized": [s.unrealized for s in equity_snapshots],
        "equity": [s.equity for s in equity_snapshots],
        "positions": [s.positions for s in equity_snapshots],
    }
    df = pd.DataFrame(data).set_index("timestamp")
    df.to_csv(run_dir / "equity_curve.csv", header=True)


def write_trades_csv(
    run_dir: Path,
    trades: List[TradeRecord],
) -> None:
    """写 trades.csv。"""
    if not trades:
        return

    data = []
    for t in trades:
        data.append({
            "symbol": t.symbol,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "size": t.size,
            "leverage": t.leverage,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "exit_reason": t.exit_reason,
            "holding_bars": t.holding_bars,
            "commission": t.commission,
        })
    df = pd.DataFrame(data)
    df.to_csv(run_dir / "trades.csv", index=False)


def write_ohlcv_snapshots(
    run_dir: Path,
    data_map: Dict[str, pd.DataFrame],
    codes: List[str],
) -> None:
    """写每个标的的 OHLCV 到子目录。"""
    ohlcv_dir = run_dir / "ohlcv"
    ohlcv_dir.mkdir(exist_ok=True)
    for code in codes:
        if code in data_map:
            df = data_map[code]
            safe_name = code.replace("/", "_").replace(".", "_")
            df.to_csv(ohlcv_dir / f"{safe_name}.csv", header=True)


def write_metrics_json(
    run_dir: Path,
    metrics: Dict[str, Any],
) -> None:
    """写 metrics.json。"""
    # JSON-safe: convert NaN/Inf to None
    def _json_safe(obj):
        if isinstance(obj, float):
            if obj != obj:  # NaN
                return None
            if obj == float("inf") or obj == float("-inf"):
                return None
        return obj

    safe_metrics = {k: _json_safe(v) for k, v in metrics.items()}
    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(safe_metrics, f, indent=2, ensure_ascii=False, default=str)


def write_all_artifacts(
    run_dir: Path,
    engine,
    data_map: Dict[str, pd.DataFrame],
    codes: List[str],
    metrics: Dict[str, Any],
) -> None:
    """写所有回测产物。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    write_equity_curve(run_dir, engine.equity_snapshots)
    write_trades_csv(run_dir, engine.trades)
    write_ohlcv_snapshots(run_dir, data_map, codes)
    write_metrics_json(run_dir, metrics)


__all__ = [
    "write_equity_curve",
    "write_trades_csv",
    "write_ohlcv_snapshots",
    "write_metrics_json",
    "write_all_artifacts",
]