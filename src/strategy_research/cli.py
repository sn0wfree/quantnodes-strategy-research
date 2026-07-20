"""quantnodes-research CLI — 策略研究工作区管理。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ============================================================
# 模板内容 (从文件加载或内嵌)
# ============================================================

def _load_template(name: str) -> str:
    """加载模板文件。"""
    template_dir = Path(__file__).parent / "templates"
    template_file = template_dir / name
    if template_file.exists():
        return template_file.read_text(encoding="utf-8")
    return ""


# ============================================================
# DuckDB 初始化 SQL
# ============================================================

DUCKDB_INIT_SQL = """
-- 因子注册表
CREATE TABLE IF NOT EXISTS factor_registry (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    factor_type VARCHAR NOT NULL,
    category VARCHAR,
    source VARCHAR,
    lookback_window INTEGER,
    strategy_name VARCHAR NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_by VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 验证缓存
CREATE TABLE IF NOT EXISTS validation_cache (
    factor_name VARCHAR NOT NULL,
    factor_code VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    ic_mean DOUBLE,
    ic_std DOUBLE,
    ir DOUBLE,
    ic_decay_1d DOUBLE,
    ic_decay_5d DOUBLE,
    ic_decay_20d DOUBLE,
    rank_ic_mean DOUBLE,
    stability_score DOUBLE,
    diversification_score DOUBLE,
    turnover DOUBLE,
    monotonicity_score DOUBLE,
    coverage DOUBLE,
    overall_score DOUBLE,
    is_valid BOOLEAN,
    fail_reasons VARCHAR,
    validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR,
    data_fingerprint VARCHAR,
    PRIMARY KEY (strategy_name, factor_name)
);

-- 回测结果
CREATE TABLE IF NOT EXISTS backtest_results (
    strategy_name VARCHAR NOT NULL,
    run VARCHAR NOT NULL,
    commit_hash VARCHAR,
    action VARCHAR,
    goal_metric DOUBLE,
    calmar DOUBLE,
    sharpe DOUBLE,
    max_dd DOUBLE,
    ann_return DOUBLE,
    ann_vol DOUBLE,
    sortino DOUBLE,
    turnover DOUBLE,
    factors_added INTEGER,
    factors_removed INTEGER,
    params_changed INTEGER,
    status VARCHAR,
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_name, run)
);

-- 数据指纹
CREATE TABLE IF NOT EXISTS data_fingerprint (
    table_name VARCHAR NOT NULL,
    strategy_name VARCHAR NOT NULL,
    fingerprint VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER,
    PRIMARY KEY (table_name, strategy_name)
);
"""


# ============================================================
# 初始化逻辑
# ============================================================

def _create_strategy(path: Path, strategy_name: str, strategy_type: str,
                     goal_metric: str) -> None:
    """创建策略目录和文件。"""
    strategy_dir = path / "strategies" / strategy_name
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "runs").mkdir(exist_ok=True)

    # program.md
    program_template = _load_template("program.md")
    if program_template:
        (strategy_dir / "program.md").write_text(
            program_template.format(
                strategy_name=strategy_name,
                strategy_type=strategy_type,
                goal_metric=goal_metric,
            ),
            encoding="utf-8",
        )

    # prepare.py
    prepare_template = _load_template("prepare.py")
    if prepare_template:
        (strategy_dir / "prepare.py").write_text(
            prepare_template.format(
                strategy_name=strategy_name,
                goal_metric=goal_metric,
            ),
            encoding="utf-8",
        )

    # strategy.py
    strategy_template = _load_template("strategy.py")
    if strategy_template:
        (strategy_dir / "strategy.py").write_text(
            strategy_template.format(strategy_name=strategy_name),
            encoding="utf-8",
        )

    # results.tsv (header only)
    results_path = strategy_dir / "runs" / "results.tsv"
    if not results_path.exists():
        results_path.write_text(
            "run\tcommit\taction\tcalmar\tsharpe\tmax_dd\t"
            "ann_return\tturnover\tfactors_added\tfactors_removed\t"
            "params_changed\tstatus\tdescription\n",
            encoding="utf-8",
        )


def _init_duckdb(path: Path) -> None:
    """初始化 DuckDB 表结构。"""
    try:
        import duckdb
    except ImportError:
        print("⚠️  duckdb 未安装，跳过 DuckDB 初始化。")
        print("   安装: pip install duckdb")
        return

    db_path = path / "data.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(DUCKDB_INIT_SQL)
    conn.close()
    print(f"✓ 初始化 DuckDB: {db_path}")


def _init_git(path: Path) -> None:
    """初始化 Git 仓库。"""
    import subprocess

    git_dir = path / ".git"
    if git_dir.exists():
        print("✓ Git 仓库已存在，跳过初始化")
        return

    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=True,
        )
        print("✓ 初始化 Git 仓库")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️  Git 初始化失败，请手动执行: git init")


def cmd_init(args: argparse.Namespace) -> int:
    """执行 init 命令。"""
    path = Path(args.path).resolve()

    # 检查路径
    if path.exists() and any(path.iterdir()):
        if not args.force:
            print(f"❌ 目录不为空: {path}")
            print("   使用 --force 强制初始化")
            return 1
        print(f"⚠️  强制初始化: {path}")

    # 交互式输入
    print(f"\n创建工作区: {path}\n")

    strategy_name = input("策略名称: ").strip()
    if not strategy_name:
        strategy_name = "my_strategy"
        print(f"  (使用默认: {strategy_name})")

    strategy_type = input("策略类型 (rotation/selection/timing/industry): ").strip()
    if not strategy_type:
        strategy_type = "rotation"
        print(f"  (使用默认: {strategy_type})")

    goal_metric = input("目标函数 [calmar]: ").strip()
    if not goal_metric:
        goal_metric = "calmar"
        print(f"  (使用默认: {goal_metric})")

    # 创建目录结构
    path.mkdir(parents=True, exist_ok=True)

    # README.md
    readme_template = _load_template("README.md")
    if readme_template:
        (path / "README.md").write_text(
            readme_template.format(strategy=strategy_name),
            encoding="utf-8",
        )
    print("✓ 创建 README.md")

    # config.yaml
    workspace_name = path.name
    config_content = f"""workspace:
  name: {workspace_name}
  default_strategy: {strategy_name}

strategies:
  - name: {strategy_name}
    type: {strategy_type}
    goal_metric: {goal_metric}
    goal_direction: maximize
"""
    (path / "config.yaml").write_text(config_content, encoding="utf-8")
    print("✓ 创建 config.yaml")

    # .prompts/
    prompts_dir = path / ".prompts"
    prompts_dir.mkdir(exist_ok=True)
    for prompt_name in ["researcher.md", "factor_analyst.md", "strategist.md", "critic.md"]:
        prompt_content = _load_template(f".prompts/{prompt_name}")
        if prompt_content:
            (prompts_dir / prompt_name).write_text(prompt_content, encoding="utf-8")
    print("✓ 创建 .prompts/ (4 个提示词)")

    # 策略目录
    _create_strategy(path, strategy_name, strategy_type, goal_metric)
    print(f"✓ 创建 strategies/{strategy_name}/")

    # DuckDB
    _init_duckdb(path)

    # Git
    _init_git(path)

    print(f"\n✅ 工作区初始化完成!")
    print(f"   请阅读 {path / 'README.md'} 开始研究。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """执行 status 命令。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    # 读取 config.yaml
    try:
        import yaml
    except ImportError:
        print("❌ 需要安装 pyyaml: pip install pyyaml")
        return 1

    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    workspace_name = config.get("workspace", {}).get("name", "unknown")
    strategies = config.get("strategies", [])

    print(f"📁 工作区: {workspace_name}")
    print(f"   路径: {path}")
    print()

    # 列出策略
    print(f"📊 策略 ({len(strategies)}):")
    for s in strategies:
        sname = s.get("name", "unknown")
        stype = s.get("type", "unknown")
        goal = s.get("goal_metric", "unknown")
        print(f"  - {sname} (类型: {stype}, 目标: {goal})")

        # 统计 runs
        runs_dir = path / "strategies" / sname / "runs"
        if runs_dir.exists():
            runs = [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
            if runs:
                # 读取 results.tsv
                results_file = runs_dir / "results.tsv"
                if results_file.exists():
                    with open(results_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    total = len(lines) - 1  # 减去 header
                    keeps = sum(1 for l in lines[1:] if "\tkeep\t" in l)
                    discards = sum(1 for l in lines[1:] if "\tdiscard\t" in l)
                    print(f"    实验: {total} 轮 (keep: {keeps}, discard: {discards})")

                    # 最后一轮
                    if total > 0:
                        last = lines[-1].strip().split("\t")
                        if len(last) >= 5:
                            print(f"    最新: {last[0]} | {last[2]} | calmar={last[3]} | {last[-1]}")
                else:
                    print(f"    实验: {len(runs)} 个目录")
            else:
                print(f"    实验: 无")
        else:
            print(f"    实验: 无 runs 目录")

    # DuckDB 状态
    db_path = path / "data.duckdb"
    if db_path.exists():
        print(f"\n🗄️  DuckDB: {db_path}")
        try:
            import duckdb
            conn = duckdb.connect(str(db_path), read_only=True)
            tables = conn.execute("SHOW TABLES").fetchall()
            for t in tables:
                count = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
                print(f"  - {t[0]}: {count} 行")
            conn.close()
        except Exception as e:
            print(f"  ⚠️  读取失败: {e}")
    else:
        print(f"\n🗄️  DuckDB: 不存在")

    return 0


def cmd_reproduce(args: argparse.Namespace) -> int:
    """执行 reproduce 命令。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    # 读取 config.yaml
    try:
        import yaml
    except ImportError:
        print("❌ 需要安装 pyyaml: pip install pyyaml")
        return 1

    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = args.strategy
    if not strategy_name:
        strategy_name = config.get("workspace", {}).get("default_strategy")
        if not strategy_name:
            print("❌ 未指定策略名称，请使用 --strategy <name>")
            return 1

    run_name = args.run
    if not run_name:
        print("❌ 请指定 run 名称，例如: quantnodes-research reproduce <path> run_0001")
        return 1

    # 检查 run 目录
    run_dir = path / "strategies" / strategy_name / "runs" / run_name
    if not run_dir.exists():
        print(f"❌ Run 目录不存在: {run_dir}")
        return 1

    # 检查 strategy.py 快照
    snapshot = run_dir / "strategy.py"
    if not snapshot.exists():
        print(f"❌ 快照文件不存在: {snapshot}")
        return 1

    # 复制快照到策略目录
    strategy_dir = path / "strategies" / strategy_name
    target = strategy_dir / "strategy.py"

    # 备份当前 strategy.py
    import shutil
    backup = strategy_dir / "strategy.py.bak"
    if target.exists():
        shutil.copy2(target, backup)
        print(f"✓ 备份当前配置: {backup}")

    # 复制快照
    shutil.copy2(snapshot, target)
    print(f"✓ 恢复快照: {run_name}/strategy.py → strategy.py")

    # 显示 metrics.json (如果存在)
    metrics_file = run_dir / "metrics.json"
    if metrics_file.exists():
        import json
        with open(metrics_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)
        print(f"\n📊 实验指标 ({run_name}):")
        for k, v in metrics.items():
            if k not in ("run", "commit", "timestamp"):
                print(f"  {k}: {v}")

    print(f"\n✅ 已恢复到 {run_name}")
    print(f"   运行策略: cd {strategy_dir} && python strategy.py")
    return 0


# ============================================================
# Main CLI
# ============================================================

def main() -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        prog="quantnodes-research",
        description="通用策略自动研究框架",
    )
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # init
    init_parser = subparsers.add_parser("init", help="初始化工作区")
    init_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    init_parser.add_argument("--force", action="store_true", help="强制初始化")

    # status
    status_parser = subparsers.add_parser("status", help="查看工作区状态")
    status_parser.add_argument("path", nargs="?", default=".", help="工作区路径")

    # reproduce
    reproduce_parser = subparsers.add_parser("reproduce", help="复现实验")
    reproduce_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    reproduce_parser.add_argument("run", nargs="?", help="Run 名称 (例如: run_0001)")
    reproduce_parser.add_argument("--strategy", "-s", help="策略名称")

    args = parser.parse_args()

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "reproduce":
        return cmd_reproduce(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
