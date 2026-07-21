"""回测执行工具。

运行策略回测，保存结果到 runs/run_XXXX/ 和 DuckDB。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .db import (
    save_backtest_result, get_backtest_results,
    save_weight_history, save_nav_history,
)
from .git import git_commit, git_get_hash
from .run_card import write_run_card


# ============================================================
# 指标解析
# ============================================================

METRIC_PATTERNS = [
    (r"^calmar:\s*([0-9.eE+-]+)$", "calmar", float),
    (r"^sharpe:\s*([0-9.eE+-]+)$", "sharpe", float),
    (r"^max_dd:\s*([0-9.eE+-]+)$", "max_dd", float),
    (r"^ann_return:\s*([0-9.eE+-]+)$", "ann_return", float),
    (r"^ann_vol:\s*([0-9.eE+-]+)$", "ann_vol", float),
    (r"^sortino:\s*([0-9.eE+-]+)$", "sortino", float),
    (r"^turnover:\s*([0-9.eE+-]+)$", "turnover", float),
    (r"^win_rate:\s*([0-9.eE+-]+)$", "win_rate", float),
]


def parse_run_log(log_path: Path) -> dict:
    """解析 run.log 提取指标。"""
    metrics = {}
    if not log_path.exists():
        return metrics

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            for pattern, name, converter in METRIC_PATTERNS:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    try:
                        metrics[name] = converter(match.group(1))
                    except (ValueError, IndexError):
                        pass
                    break

    return metrics


# ============================================================
# Run 目录管理
# ============================================================

def get_next_run_name(strategy_dir: Path) -> str:
    """获取下一个 run 名称。"""
    runs_dir = strategy_dir / "runs"
    if not runs_dir.exists():
        return "run_0001"

    existing = []
    for d in runs_dir.iterdir():
        if d.is_dir() and d.name.startswith("run_"):
            try:
                num = int(d.name.split("_")[1])
                existing.append(num)
            except (ValueError, IndexError):
                pass

    if not existing:
        return "run_0001"

    next_num = max(existing) + 1
    return f"run_{next_num:04d}"


def create_run_dir(strategy_dir: Path, run_name: str) -> Path:
    """创建 run 目录。"""
    run_dir = strategy_dir / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_run_snapshot(strategy_dir: Path, run_dir: Path) -> None:
    """保存策略快照。"""
    src = strategy_dir / "strategy.py"
    dst = run_dir / "strategy.py"
    if src.exists():
        shutil.copy2(src, dst)

    # 也保存 config.yaml
    config_src = strategy_dir / "config.yaml"
    config_dst = run_dir / "config.yaml"
    if config_src.exists():
        shutil.copy2(config_src, config_dst)


def save_run_metrics(run_dir: Path, metrics: dict) -> None:
    """保存 metrics.json。"""
    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)


def update_results_tsv(strategy_dir: Path, run_name: str, metrics: dict) -> None:
    """更新 results.tsv。"""
    results_path = strategy_dir / "runs" / "results.tsv"

    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\t"
            "ann_return\tturnover\tfactors_added\tfactors_removed\t"
            "params_changed\tstatus\tdescription\n"
        ]

    row = "\t".join([
        run_name,
        metrics.get("commit", ""),
        metrics.get("action", ""),
        str(metrics.get("calmar", 0.0)),
        str(metrics.get("sharpe", 0.0)),
        str(metrics.get("max_dd", 0.0)),
        str(metrics.get("ann_return", 0.0)),
        str(metrics.get("turnover", 0.0)),
        str(metrics.get("factors_added", 0)),
        str(metrics.get("factors_removed", 0)),
        str(metrics.get("params_changed", 0)),
        metrics.get("status", "pending"),
        metrics.get("description", ""),
    ]) + "\n"

    lines.append(row)

    with open(results_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ============================================================
# 回测执行 (脚本模式)
# ============================================================

def _extract_warnings(metrics: dict, output: str) -> list[str]:
    """从 metrics 和 output 中提取 warnings (用于 run_card).

    v1 极简版: 仅检测 NaN/Inf 字段, 后续可扩展.
    """
    warnings: list[str] = []
    for k, v in metrics.items():
        if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
            warnings.append(f"invalid_{k}: {v!r}")
    return warnings


def run_strategy(
    strategy_dir: Path,
    timeout: int = 300,
) -> tuple[bool, str]:
    """运行策略脚本。"""
    strategy_file = strategy_dir / "strategy.py"
    if not strategy_file.exists():
        return False, f"策略文件不存在: {strategy_file}"

    try:
        import sys
        result = subprocess.run(
            [sys.executable, str(strategy_file)],
            cwd=str(strategy_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = result.stdout + "\n" + result.stderr
        success = result.returncode == 0

        return success, output

    except subprocess.TimeoutExpired:
        return False, f"策略执行超时 ({timeout}秒)"
    except Exception as e:
        return False, f"策略执行失败: {e}"


def run_backtest_script(
    workspace_path: Path,
    strategy_name: str,
    action: str = "manual",
    description: str = "",
    timeout: int = 300,
) -> dict:
    """运行策略脚本回测并保存结果。"""
    strategy_dir = workspace_path / "strategies" / strategy_name
    if not strategy_dir.exists():
        return {"success": False, "run": "", "metrics": {}, "error": f"策略目录不存在: {strategy_dir}"}

    run_name = get_next_run_name(strategy_dir)
    run_dir = create_run_dir(strategy_dir, run_name)

    save_run_snapshot(strategy_dir, run_dir)

    success, output = run_strategy(strategy_dir, timeout)

    with open(run_dir / "run.log", "w", encoding="utf-8") as f:
        f.write(output)

    metrics = parse_run_log(run_dir / "run.log")

    commit_hash = git_get_hash(workspace_path)

    metrics.update({
        "run": run_name,
        "commit": commit_hash,
        "action": action,
        "description": description,
        "status": "pending",
        "timestamp": datetime.now().isoformat(),
    })

    save_run_metrics(run_dir, metrics)
    update_results_tsv(strategy_dir, run_name, metrics)

    save_backtest_result(
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        run=run_name,
        commit_hash=commit_hash,
        action=action,
        goal_metric=metrics.get("calmar", 0.0),
        calmar=metrics.get("calmar", 0.0),
        sharpe=metrics.get("sharpe", 0.0),
        max_dd=metrics.get("max_dd", 0.0),
        ann_return=metrics.get("ann_return", 0.0),
        ann_vol=metrics.get("ann_vol", 0.0),
        sortino=metrics.get("sortino", 0.0),
        turnover=metrics.get("turnover", 0.0),
        status="pending",
        description=description,
    )

    # Trust Layer: write run_card.{json,md}
    write_run_card(
        run_dir,
        config={
            "run": run_name,
            "strategy": strategy_name,
            "action": action,
        },
        metrics=metrics,
        strategy_paths=[
            run_dir / "strategy.py",
            run_dir / "config.yaml",
        ],
        warnings=_extract_warnings(metrics, output),
    )

    return {
        "success": success,
        "run": run_name,
        "metrics": metrics,
        "error": "" if success else output,
    }


# ============================================================
# 回测执行 (YAML 配置模式)
# ============================================================

def run_backtest_from_yaml(
    workspace_path: Path,
    strategy_name: str,
    yaml_path: str | None = None,
    action: str = "manual",
    description: str = "",
) -> dict:
    """从 YAML 配置运行回测。"""
    if yaml_path is None:
        yaml_path = workspace_path / "strategies" / strategy_name / "config.yaml"
    else:
        yaml_path = Path(yaml_path)

    if not yaml_path.exists():
        return {"success": False, "run": "", "metrics": {}, "error": f"配置文件不存在: {yaml_path}"}

    try:
        from .config_runner import run_from_yaml
        result = run_from_yaml(str(yaml_path), workspace_path)

        # 保存结果
        strategy_dir = workspace_path / "strategies" / strategy_name
        run_name = get_next_run_name(strategy_dir)
        run_dir = create_run_dir(strategy_dir, run_name)

        save_run_snapshot(strategy_dir, run_dir)

        # 保存 metrics.json
        metrics = result.metrics.copy()
        metrics.update({
            "run": run_name,
            "commit": git_get_hash(workspace_path),
            "action": action,
            "description": description,
            "status": "pending",
            "timestamp": datetime.now().isoformat(),
        })
        save_run_metrics(run_dir, metrics)
        update_results_tsv(strategy_dir, run_name, metrics)

        # 保存到 DuckDB
        save_backtest_result(
            workspace_path=workspace_path,
            strategy_name=strategy_name,
            run=run_name,
            commit_hash=metrics.get("commit", ""),
            action=action,
            goal_metric=metrics.get("calmar", 0.0),
            calmar=metrics.get("calmar", 0.0),
            sharpe=metrics.get("sharpe", 0.0),
            max_dd=metrics.get("max_dd", 0.0),
            ann_return=metrics.get("ann_return", 0.0),
            ann_vol=metrics.get("ann_vol", 0.0),
            sortino=metrics.get("sortino", 0.0),
            turnover=metrics.get("turnover", 0.0),
            status="pending",
            description=description,
        )

        # 保存权重历史和 NAV 历史到 DuckDB
        save_weight_history(workspace_path, strategy_name, run_name, result.weights_history)
        save_nav_history(workspace_path, strategy_name, run_name, result.nav_daily)

        # Trust Layer: write run_card.{json,md}
        write_run_card(
            run_dir,
            config={
                "run": run_name,
                "strategy": strategy_name,
                "action": action,
            },
            metrics=metrics,
            strategy_paths=[
                run_dir / "strategy.py",
                run_dir / "config.yaml",
            ],
        )

        return {
            "success": True,
            "run": run_name,
            "metrics": metrics,
            "nav": result.nav_daily,
        }

    except Exception as e:
        return {"success": False, "run": "", "metrics": {}, "error": str(e)}


# ============================================================
# 实验评估
# ============================================================

def evaluate_experiment(
    workspace_path: Path,
    strategy_name: str,
    run_name: str,
    status: str = "keep",
) -> bool:
    """评估实验结果并更新状态。"""
    strategy_dir = workspace_path / "strategies" / strategy_name
    run_dir = strategy_dir / "runs" / run_name

    if not run_dir.exists():
        return False

    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return False

    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    metrics["status"] = status

    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    update_results_tsv(strategy_dir, run_name, metrics)

    from .db import get_connection
    conn = get_connection(workspace_path)
    if conn is not None:
        try:
            conn.execute("""
                UPDATE backtest_results
                SET status = ?
                WHERE strategy_name = ? AND run = ?
            """, [status, strategy_name, run_name])
            conn.close()
        except Exception:
            pass

    if status == "keep":
        git_commit(workspace_path, f"keep: {strategy_name}/{run_name}")

    return True


# ============================================================
# 实验历史
# ============================================================

def get_experiment_history(
    workspace_path: Path,
    strategy_name: str,
    limit: int = 20,
) -> list[dict]:
    """获取实验历史。"""
    strategy_dir = workspace_path / "strategies" / strategy_name
    results_path = strategy_dir / "runs" / "results.tsv"

    if not results_path.exists():
        return []

    with open(results_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) <= 1:
        return []

    header = lines[0].rstrip("\n").split("\t")

    experiments = []
    for line in lines[1:limit + 1]:
        # 用 rstrip 保留 trailing empty (zip header 长度对齐)
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= len(header):
            exp = dict(zip(header, parts))
            experiments.append(exp)

    return experiments


def get_best_experiment(
    workspace_path: Path,
    strategy_name: str,
    goal_metric: str = "calmar",
) -> Optional[dict]:
    """获取最佳实验。"""
    experiments = get_experiment_history(workspace_path, strategy_name)
    if not experiments:
        return None

    keeps = [e for e in experiments if e.get("status") == "keep"]
    if not keeps:
        return None

    def get_metric(exp):
        try:
            return float(exp.get(goal_metric, 0))
        except (ValueError, TypeError):
            return 0

    return max(keeps, key=get_metric)
