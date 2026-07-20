"""回测执行工具。

运行策略回测，保存结果到 runs/run_XXXX/。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .db import save_backtest_result, get_backtest_results
from .git import git_commit, git_get_hash


# ============================================================
# 指标解析
# ============================================================

# 支持的指标格式
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
    """解析 run.log 提取指标。

    Args:
        log_path: run.log 路径

    Returns:
        dict: 提取的指标
    """
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


def parse_metrics_json(metrics_path: Path) -> dict:
    """解析 metrics.json。"""
    if not metrics_path.exists():
        return {}

    with open(metrics_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# Run 目录管理
# ============================================================

def get_next_run_name(strategy_dir: Path) -> str:
    """获取下一个 run 名称。"""
    runs_dir = strategy_dir / "runs"
    if not runs_dir.exists():
        return "run_0001"

    # 查找现有 run 目录
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


def save_run_metrics(run_dir: Path, metrics: dict) -> None:
    """保存 metrics.json。"""
    with open(run_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)


def update_results_tsv(strategy_dir: Path, run_name: str, metrics: dict) -> None:
    """更新 results.tsv。"""
    results_path = strategy_dir / "runs" / "results.tsv"

    # 读取 header
    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = [
            "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\t"
            "ann_return\tturnover\tfactors_added\tfactors_removed\t"
            "params_changed\tstatus\tdescription\n"
        ]

    # 构建新行
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
# 回测执行
# ============================================================

def run_strategy(
    strategy_dir: Path,
    timeout: int = 300,
) -> tuple[bool, str]:
    """运行策略脚本。

    Args:
        strategy_dir: 策略目录
        timeout: 超时时间 (秒)

    Returns:
        tuple: (success, output)
    """
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


def run_backtest(
    workspace_path: Path,
    strategy_name: str,
    action: str = "manual",
    description: str = "",
    timeout: int = 300,
) -> dict:
    """运行策略回测并保存结果。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        action: 行动类型 (manual/search_external/discover_local/optimize_param/remove_factor)
        description: 描述
        timeout: 超时时间

    Returns:
        dict: {
            "success": bool,
            "run": str,
            "metrics": dict,
            "error": str,
        }
    """
    strategy_dir = workspace_path / "strategies" / strategy_name
    if not strategy_dir.exists():
        return {"success": False, "run": "", "metrics": {}, "error": f"策略目录不存在: {strategy_dir}"}

    # 获取 run 名称
    run_name = get_next_run_name(strategy_dir)
    run_dir = create_run_dir(strategy_dir, run_name)

    # 保存快照
    save_run_snapshot(strategy_dir, run_dir)

    # 运行策略
    success, output = run_strategy(strategy_dir, timeout)

    # 保存 run.log
    with open(run_dir / "run.log", "w", encoding="utf-8") as f:
        f.write(output)

    # 解析指标
    metrics = parse_run_log(run_dir / "run.log")

    # 获取 git commit hash
    commit_hash = git_get_hash(workspace_path)

    # 构建完整 metrics
    metrics.update({
        "run": run_name,
        "commit": commit_hash,
        "action": action,
        "description": description,
        "status": "pending",
        "timestamp": datetime.now().isoformat(),
    })

    # 保存 metrics.json
    save_run_metrics(run_dir, metrics)

    # 更新 results.tsv
    update_results_tsv(strategy_dir, run_name, metrics)

    # 保存到 DuckDB
    save_backtest_result(
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        run=run_name,
        commit_hash=commit_hash,
        action=action,
        goal_metric=metrics.get("calmar", 0.0),  # 默认目标函数
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

    return {
        "success": success,
        "run": run_name,
        "metrics": metrics,
        "error": "" if success else output,
    }


# ============================================================
# 实验评估
# ============================================================

def evaluate_experiment(
    workspace_path: Path,
    strategy_name: str,
    run_name: str,
    status: str = "keep",
) -> bool:
    """评估实验结果并更新状态。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称
        run_name: run 名称
        status: "keep" 或 "discard"

    Returns:
        bool: 是否成功
    """
    strategy_dir = workspace_path / "strategies" / strategy_name
    run_dir = strategy_dir / "runs" / run_name

    if not run_dir.exists():
        return False

    # 读取 metrics.json
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return False

    with open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    # 更新状态
    metrics["status"] = status

    # 保存更新后的 metrics.json
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    # 更新 results.tsv
    update_results_tsv(strategy_dir, run_name, metrics)

    # 更新 DuckDB
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

    # Git 操作
    if status == "keep":
        git_commit(workspace_path, f"keep: {strategy_name}/{run_name}")
    elif status == "discard":
        # 不自动 reset，只标记
        pass

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

    # 解析 header
    header = lines[0].strip().split("\t")

    # 解析数据行
    experiments = []
    for line in lines[1:limit + 1]:
        parts = line.strip().split("\t")
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

    # 过滤 keep 的实验
    keeps = [e for e in experiments if e.get("status") == "keep"]
    if not keeps:
        return None

    # 按目标函数排序
    def get_metric(exp):
        try:
            return float(exp.get(goal_metric, 0))
        except (ValueError, TypeError):
            return 0

    return max(keeps, key=get_metric)
