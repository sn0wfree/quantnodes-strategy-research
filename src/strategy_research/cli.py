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


def _render_template(template: str, **kwargs) -> str:
    """替换 {key} 占位符，但保留 {} 字面量 (Python dict literal 等)。

    与 str.format() 的区别：本函数用 str.replace 逐个替换已知的 {key} 占位符，
    不会把 Python 代码中的空 dict {} 误判为位置参数。
    """
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


# ============================================================
# DuckDB 初始化 SQL
# ============================================================
# 注意：DUCKDB_INIT_SQL 的权威定义在 core.db.DUCKDB_INIT_SQL（含 price_data /
# factor_data / weight_history / nav_history / import_meta 共 8 张表）。
# 这里不再重复定义，_init_duckdb() 直接调用 core.db.init_db()。


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
            _render_template(
                program_template,
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
            _render_template(
                prepare_template,
                strategy_name=strategy_name,
                goal_metric=goal_metric,
            ),
            encoding="utf-8",
        )

    # strategy.py
    strategy_template = _load_template("strategy.py")
    if strategy_template:
        (strategy_dir / "strategy.py").write_text(
            _render_template(strategy_template, strategy_name=strategy_name),
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
    """初始化 DuckDB 表结构。委托给 core.db.init_db() 使用完整 schema。"""
    from .core.db import init_db

    ok = init_db(path)
    if ok:
        print(f"✓ 初始化 DuckDB: {path / 'data.duckdb'}")


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




def _run_baseline_backtest(workspace_path: Path, strategy_name: str, strategy_dir: Path) -> None:
    """运行 baseline 回测 (默认 momentum_20_60 因子)。"""
    from .core.data_import import generate_sample_data, import_dataframe
    from .core.backtest import run_backtest_script

    # 生成模拟数据 (10 资产 × 504 天)
    prices = generate_sample_data(n_assets=10, n_days=504, start_date="2022-01-01")

    # 灌入 DuckDB (close 面板)
    import_dataframe(workspace_path, strategy_name, prices)

    # 运行 baseline 回测
    result = run_backtest_script(
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        action="baseline",
        description="Default momentum_20_60 baseline",
    )

    if result.get("success"):
        m = result["metrics"]
        print(f"  baseline: Calmar={m.get('calmar', 0):.3f} "
              f"Sharpe={m.get('sharpe', 0):.3f} "
              f"MaxDD={m.get('max_dd', 0):.3f} "
              f"AnnRet={m.get('ann_return', 0):.3f}")
    else:
        print(f"  baseline 回测失败: {result.get('error', 'unknown')}")

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
            _render_template(readme_template, strategy=strategy_name),
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

# 数据源 (cmd import 时使用)
data:
  source: duckdb
  incremental: true
  codes:
    - 000300.SH  # 沪深 300（示例，可改为具体股票）

# 回测参数 (cmd run / cmd evaluate 时使用)
rebalance:
  freq: M                      # M=月度, W=周度, Q=季度
  min_history: 252

top_n: 10
max_weight: 0.25
weight_method: inverse_vol     # inverse_vol/equal

# 交易成本
cost:
  enabled: true
  commission_bp: 5
  slippage_bp: 10
  impact_factor: 0.1

# 风险控制
risk:
  vol_targeting:
    enabled: false
    target_vol: 0.15
  trend_filter:
    enabled: false
  stop_loss:
    enabled: false
"""
    (path / "config.yaml").write_text(config_content, encoding="utf-8")
    print("✓ 创建 config.yaml")

    # .prompts/
    prompts_dir = path / ".prompts"
    prompts_dir.mkdir(exist_ok=True)
    for prompt_name in [
        "orchestrator.md", "data_quality.md", "researcher.md", "factor_analyst.md",
        "strategist.md", "portfolio_construction.md", "risk_controller.md",
        "attribution_analyst.md", "anti_overfit_analyst.md", "backtest_diagnostics.md",
        "critic.md",
    ]:
        prompt_content = _load_template(f".prompts/{prompt_name}")
        if prompt_content:
            (prompts_dir / prompt_name).write_text(prompt_content, encoding="utf-8")
    print("✓ 创建 .prompts/ (11 个提示词)")

    # .skills/
    skills_dir = path / ".skills"
    skills_dir.mkdir(exist_ok=True)
    for skill_name in [
        "data-routing.md", "factor-research.md", "backtest-diagnose.md",
        "correlation-analysis.md", "ml-strategy.md", "performance-attribution.md",
        "quant-statistics.md", "risk-analysis.md", "sector-rotation.md",
        "research-discipline.md",
    ]:
        skill_content = _load_template(f".skills/{skill_name}")
        if skill_content:
            (skills_dir / skill_name).write_text(skill_content, encoding="utf-8")
    print("✓ 创建 .skills/ (10 份方法论)")

    # 策略目录
    _create_strategy(path, strategy_name, strategy_type, goal_metric)
    print(f"✓ 创建 strategies/{strategy_name}/")

    # DuckDB
    _init_duckdb(path)

    # Git
    _init_git(path)

    # Baseline 回测 (buy and hold HS300)
    if not args.no_baseline:
        try:
            strategy_dir = path / "strategies" / strategy_name
            _run_baseline_backtest(path, strategy_name, strategy_dir)
            print("✓ 运行 baseline 回测 (buy and hold HS300)")
        except Exception as e:
            print(f"⚠️  baseline 回测失败: {e}")

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


def cmd_run(args: argparse.Namespace) -> int:
    """执行 run 命令 - 运行回测。"""
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

    from strategy_research.core.backtest import run_backtest_script as run_backtest
    result = run_backtest(
        workspace_path=path,
        strategy_name=strategy_name,
        action=args.action or "manual",
        description=args.description or "",
        timeout=args.timeout,
    )

    if result["success"]:
        print(f"✅ 回测完成: {result['run']}")
        metrics = result["metrics"]
        print(f"   calmar: {metrics.get('calmar', 'N/A')}")
        print(f"   sharpe: {metrics.get('sharpe', 'N/A')}")
        print(f"   max_dd: {metrics.get('max_dd', 'N/A')}")
        print(f"   ann_return: {metrics.get('ann_return', 'N/A')}")
    else:
        print(f"❌ 回测失败: {result['error'][:200]}")
        return 1

    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """执行 evaluate 命令 — 重跑当前 strategy.py 并写新 run_XXXX。

    与 run 的区别: evaluate 不需要 action / description,
    专为手动复跑 baseline / 验证修改后的 strategy.py 设计。
    """
    path = Path(args.path).resolve()

    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

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

    from .core.backtest import run_backtest_script

    print(f"🔄 复跑策略: {strategy_name}")
    result = run_backtest_script(
        workspace_path=path,
        strategy_name=strategy_name,
        action="evaluate",
        description=args.description or "Manual re-evaluation via `evaluate`",
        timeout=args.timeout,
    )

    if result["success"]:
        m = result["metrics"]
        print(f"\n✅ 复跑成功: {result['run']}")
        print(f"   Calmar   = {m.get('calmar', 0):.4f}")
        print(f"   Sharpe   = {m.get('sharpe', 0):.4f}")
        print(f"   MaxDD    = {m.get('max_dd', 0):.4f}")
        print(f"   AnnRet   = {m.get('ann_return', 0):.4f}")
        print(f"   AnnVol   = {m.get('ann_vol', 0):.4f}")
        print(f"   Sortino  = {m.get('sortino', 0):.4f}")
        print(f"   Turnover = {m.get('turnover', 0):.4f}")
        print(f"\n📁 详见: {path / 'strategies' / strategy_name / 'runs' / result['run']}")
        return 0
    else:
        print(f"\n❌ 复跑失败: {result.get('error', 'unknown')[:300]}")
        return 1


def cmd_preflight(args: argparse.Namespace) -> int:
    """执行 preflight 命令 — 启动前环境检查。

    检查项：
    - LLM Provider key (critical)
    - DuckDB 可写
    - 至少一个数据源可用
    - OHLCV 数据完整性

    Critical 失败时返回 1，否则 0。
    """
    from .core.preflight import run_preflight

    workspace = Path(args.path).resolve() if args.path else Path.cwd()
    results = run_preflight(workspace, verbose=True)

    critical_failed = [r for r in results if r.critical and r.status != "ready"]
    return 1 if critical_failed else 0


def cmd_validate(args: argparse.Namespace) -> int:
    """执行 validate 命令 - 验证因子。"""
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

    factor_code = args.factor
    if not factor_code:
        print("❌ 请指定因子表达式，例如: --factor 'ts_return(close, 20)'")
        return 1

    import pandas as pd
    from strategy_research.core.factor_validate import validate_factor

    # 加载价格数据 (简单示例: 从 CSV 加载)
    prices_file = path / "data" / "prices.csv"
    if not prices_file.exists():
        print(f"❌ 价格数据不存在: {prices_file}")
        print("   请将价格数据保存到 data/prices.csv (index=date, columns=assets)")
        return 1

    prices = pd.read_csv(prices_file, index_col=0, parse_dates=True)

    # 验证因子
    result = validate_factor(
        factor_code=factor_code,
        prices=prices,
        strategy_name=strategy_name,
        source=args.source or "cli",
    )

    print(f"\n📊 因子验证结果: {factor_code}")
    print(f"   通过: {'✓' if result['passed'] else '❌'}")
    print(f"   IC mean: {result['ic_mean']:.4f}")
    print(f"   IR: {result['ir']:.4f}")
    print(f"   Rank IC: {result['rank_ic_mean']:.4f}")
    print(f"   综合评分: {result['overall_score']:.4f}")

    if result["fail_reasons"]:
        print(f"\n   失败原因:")
        for reason in result["fail_reasons"]:
            print(f"     - {reason}")

    if result["scores"]:
        print(f"\n   6 维评分:")
        for k, v in result["scores"].items():
            print(f"     {k}: {v:.4f}")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """执行 list 命令 - 列出实验。"""
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

    from strategy_research.core.backtest import get_experiment_history, get_best_experiment

    experiments = get_experiment_history(path, strategy_name, limit=args.limit)

    if not experiments:
        print(f"📭 没有实验记录")
        return 0

    print(f"\n📋 实验记录 ({strategy_name}):")
    print(f"{'run':<12} {'action':<20} {'calmar':<10} {'sharpe':<10} {'status':<10} {'description'}")
    print("-" * 80)

    for exp in experiments:
        run = exp.get("run", "")
        action = exp.get("action", "")
        calmar = exp.get("calmar", "N/A")
        sharpe = exp.get("sharpe", "N/A")
        status = exp.get("status", "")
        desc = exp.get("description", "")

        # 状态标记
        status_mark = "✓" if status == "keep" else "✗" if status == "discard" else "○"

        print(f"{run:<12} {action:<20} {calmar:<10} {sharpe:<10} {status_mark} {desc}")

    # 显示最佳实验
    best = get_best_experiment(path, strategy_name)
    if best:
        print(f"\n🏆 最佳实验: {best.get('run')} (calmar={best.get('calmar')})")

    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """执行 import 命令 - 导入价格数据。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    from strategy_research.core.data_import import (
        import_csv, import_parquet, generate_sample_data, import_dataframe,
        import_tushare, import_ifind, import_fred, import_akshare, import_from_source,
    )
    from strategy_research.core.db import init_db

    # 确保 DuckDB 初始化
    init_db(path)

    strategy_name = args.strategy
    source = args.source

    # 本地文件源
    if source == "sample":
        prices = generate_sample_data(
            n_assets=args.n_assets,
            n_days=args.n_days,
        )
        success = import_dataframe(path, strategy_name, prices)

    elif source == "csv":
        if not args.file:
            print("❌ 请指定 --file 参数")
            return 1
        success = import_csv(
            path, strategy_name, args.file,
            date_column=args.date_column,
            price_column=args.price_column,
            asset_column=args.asset_column,
        )

    elif source == "parquet":
        if not args.file:
            print("❌ 请指定 --file 参数")
            return 1
        success = import_parquet(path, strategy_name, args.file)

    # API 数据源
    elif source == "tushare":
        if not args.codes:
            print("❌ 请指定 --codes 参数 (如: 000001.SZ,600519.SH)")
            return 1
        codes = [c.strip() for c in args.codes.split(",")]
        success = import_tushare(
            path, strategy_name, codes,
            args.start_date, args.end_date,
            incremental=args.incremental,
        )

    elif source == "ifind":
        if not args.codes:
            print("❌ 请指定 --codes 参数")
            return 1
        codes = [c.strip() for c in args.codes.split(",")]
        success = import_ifind(
            path, strategy_name, codes,
            args.start_date, args.end_date,
            incremental=args.incremental,
        )

    elif source == "fred":
        if not args.codes:
            # 默认导入核心系列
            from strategy_research.core.data_source.fred_loader import CORE_SERIES
            codes = CORE_SERIES
            print(f"📡 导入 FRED 核心系列 ({len(codes)} 个)...")
        else:
            codes = [c.strip() for c in args.codes.split(",")]
        success = import_fred(
            path, strategy_name, codes,
            args.start_date, args.end_date,
            incremental=args.incremental,
        )

    elif source == "akshare":
        if not args.codes:
            print("❌ 请指定 --codes 参数")
            return 1
        codes = [c.strip() for c in args.codes.split(",")]
        success = import_akshare(
            path, strategy_name, codes,
            args.start_date, args.end_date,
            incremental=args.incremental,
        )

    elif source == "auto":
        if not args.codes:
            print("❌ 请指定 --codes 参数")
            return 1
        codes = [c.strip() for c in args.codes.split(",")]
        success = import_from_source(
            path, strategy_name, "auto", codes,
            args.start_date, args.end_date,
            incremental=args.incremental,
        )

    else:
        print(f"❌ 未知数据源: {source}")
        print(f"   支持: csv, parquet, sample, tushare, ifind, fred, akshare, auto")
        return 1

    if success:
        print(f"\n✅ 数据导入完成")
        from strategy_research.core.db import get_price_data_info
        info = get_price_data_info(path, strategy_name)
        if info:
            print(f"   资产数: {info.get('n_assets', 0)}")
            print(f"   日期数: {info.get('n_dates', 0)}")
            print(f"   时间范围: {info.get('start_date')} ~ {info.get('end_date')}")
        return 0
    else:
        print(f"\n❌ 数据导入失败")
        return 1


def cmd_autoresearch(args: argparse.Namespace) -> int:
    """执行 autoresearch 命令 - 运行自动化研究循环。"""
    import json
    import time
    import random
    from .core.autoresearch import (
        build_agent_prompt, save_agent_record, read_current_state,
        parse_agent_output, retry_agent_spawn, get_cooldown_seconds,
        should_run_lazy_detection, read_agent_history, detect_lazy_behavior, save_laziness_report,
        generate_run_summary, save_run_summary, load_run_summary, DEFAULT_KEEP_RECENT,
    )
    from .core.backtest import run_backtest_script

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

    # 速度控制参数
    base_cooldown = args.cooldown or 30.0
    jitter = args.jitter or 10.0
    min_cooldown = args.min_cooldown or 1.0
    max_retries = args.max_retries or 3

    print(f"\n🚀 启动 autoresearch 循环")
    print(f"   策略: {strategy_name}")
    print(f"   cooldown: {base_cooldown}s ± {jitter}s (MIN={min_cooldown}s)")
    print(f"   max_retries: {max_retries}")
    print()

    # 主循环
    round_num = 0
    while True:
        round_num += 1
        round_start = time.time()

        print(f"{'='*60}")
        print(f"📍 第 {round_num} 轮研究")
        print(f"{'='*60}")

        # Step 1: 读状态
        print("\n[Step 1] 读取状态...")
        current_state = read_current_state(path, strategy_name)
        print(f"  最佳 Calmar: {current_state['best_calmar']:.4f}")
        print(f"  总轮数: {current_state['total_runs']}")

        # 创建 run 目录 (提前创建,避免 lazy detection 时重复创建)
        runs_dir = path / "strategies" / strategy_name / "runs"
        # 使用 max(num) + 1 与 backtest 模块保持一致
        existing_nums = []
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name.startswith("run_"):
                try:
                    existing_nums.append(int(d.name.split("_")[1]))
                except (ValueError, IndexError):
                    pass
        run_num = max(existing_nums, default=0) + 1
        run_name = f"run_{run_num:04d}"
        run_dir = runs_dir / run_name
        run_dir.mkdir(exist_ok=True)
        (run_dir / "agents").mkdir(exist_ok=True)

        # Lazy Detection (每 N 轮检测)
        lazy_detection_interval = args.lazy_detection_interval or 10
        keep_recent = args.keep_recent or DEFAULT_KEEP_RECENT
        if should_run_lazy_detection(round_num, lazy_detection_interval):
            print(f"\n[Lazy Detection] 检测 Agent 行为 (每 {lazy_detection_interval} 轮)...")
            lazy_results = []

            # 读取最近 10 轮的 agent 记录 (分层读取: 详细/摘要)
            for agent_name in ["researcher", "factor_analyst", "strategist", "anti_overfit_analyst"]:
                history = read_agent_history(
                    runs_dir, agent_name, threshold=10,
                    current_round=round_num, keep_recent=keep_recent,
                )
                if history:
                    last_output = history[-1].get("output", {})
                    lazy_result = detect_lazy_behavior(agent_name, last_output, history)
                    lazy_results.append({"agent": agent_name, **lazy_result})
                    if lazy_result["issues"]:
                        print(f"  ⚠️ {agent_name}: {lazy_result['issues']}")
            
            # 保存报告
            if lazy_results:
                overall_score = sum(r.get("lazy_score", 0) for r in lazy_results) / len(lazy_results)
                save_laziness_report(run_dir, round_num, lazy_results, overall_score)
                print(f"✅ 保存 laziness report: {run_dir}/laziness_report.json")

        # Step 2: spawn Researcher
        print("\n[Step 2] spawn Researcher...")
        researcher_output = retry_agent_spawn(
            lambda: _spawn_agent("researcher", path, strategy_name, current_state, []),
            "researcher",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "researcher", 2, current_state, researcher_output)
        print(f"  action: {researcher_output.get('action', '?')}")
        print(f"  hypothesis: {researcher_output.get('hypothesis', '?')[:50]}...")

        # Step 3: 执行 (强制执行所有 Agent)
        print("\n[Step 3] 执行...")

        # 3.1 Data Quality
        print("\n  [3.1] Data Quality...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        data_quality_output = retry_agent_spawn(
            lambda: _spawn_agent("data_quality", path, strategy_name, current_state, [researcher_output]),
            "data_quality",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "data_quality", 3, {"researcher": researcher_output}, data_quality_output)
        print(f"    passed: {data_quality_output.get('passed', '?')}")
        print(f"    warnings: {len(data_quality_output.get('warnings', []))}")

        # 3.2 Factor Analyst
        print("\n  [3.2] Factor Analyst...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        factor_analyst_output = retry_agent_spawn(
            lambda: _spawn_agent("factor_analyst", path, strategy_name, current_state, [researcher_output, data_quality_output]),
            "factor_analyst",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "factor_analyst", 3, {"researcher": researcher_output, "data_quality": data_quality_output}, factor_analyst_output)
        print(f"    candidates: {len(factor_analyst_output.get('candidates', []))}")
        print(f"    rejected: {len(factor_analyst_output.get('rejected', []))}")

        # 3.3 Strategist
        print("\n  [3.3] Strategist...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        strategist_output = retry_agent_spawn(
            lambda: _spawn_agent("strategist", path, strategy_name, current_state, [researcher_output, data_quality_output, factor_analyst_output]),
            "strategist",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "strategist", 3, {"researcher": researcher_output, "factor_analyst": factor_analyst_output}, strategist_output)
        print(f"    action: {strategist_output.get('action', '?')}")
        print(f"    changes: {len(strategist_output.get('changes', []))}")

        # 3.4 Portfolio Construction (强制执行)
        print("\n  [3.4] Portfolio Construction...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        portfolio_construction_output = retry_agent_spawn(
            lambda: _spawn_agent("portfolio_construction", path, strategy_name, current_state, [strategist_output]),
            "portfolio_construction",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "portfolio_construction", 3, {"strategist": strategist_output}, portfolio_construction_output)
        print(f"    method: {portfolio_construction_output.get('method', '?')}")
        print(f"    portfolio_vol: {portfolio_construction_output.get('portfolio_vol', '?')}")

        # Step 4: 运行回测
        print("\n[Step 4] 运行回测...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"  cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        backtest_result = run_backtest_script(
            workspace_path=path,
            strategy_name=strategy_name,
            action=strategist_output.get("action", "unknown"),
            description=strategist_output.get("hypothesis", ""),
            run_dir=run_dir,  # 使用已创建的 run_dir,避免创建额外的空目录
        )

        if backtest_result.get("success"):
            metrics = backtest_result.get("metrics", {})
            print(f"  Calmar: {metrics.get('calmar', 'N/A')}")
            print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
            print(f"  MaxDD: {metrics.get('max_dd', 'N/A')}")
        else:
            print(f"  ❌ 回测失败: {backtest_result.get('error', 'unknown')}")
            metrics = {}

        # Step 5: 评估 (强制执行所有 Agent)
        print("\n[Step 5] 评估...")

        # 5.1 Risk Controller
        print("\n  [5.1] Risk Controller...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        risk_controller_output = retry_agent_spawn(
            lambda: _spawn_agent("risk_controller", path, strategy_name, current_state, [metrics]),
            "risk_controller",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "risk_controller", 5, {"metrics": metrics}, risk_controller_output)
        print(f"    risk_passed: {risk_controller_output.get('risk_passed', '?')}")
        print(f"    risk_rating: {risk_controller_output.get('risk_rating', '?')}")

        # 5.2 Attribution Analyst
        print("\n  [5.2] Attribution Analyst...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        attribution_analyst_output = retry_agent_spawn(
            lambda: _spawn_agent("attribution_analyst", path, strategy_name, current_state, [metrics, risk_controller_output]),
            "attribution_analyst",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "attribution_analyst", 5, {"metrics": metrics, "risk_controller": risk_controller_output}, attribution_analyst_output)
        print(f"    alpha: {attribution_analyst_output.get('alpha', '?')}")
        print(f"    beta_mkt: {attribution_analyst_output.get('beta_mkt', '?')}")

        # 5.3 Anti-overfit Analyst
        print("\n  [5.3] Anti-overfit Analyst...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        anti_overfit_analyst_output = retry_agent_spawn(
            lambda: _spawn_agent("anti_overfit_analyst", path, strategy_name, current_state, [metrics, risk_controller_output, attribution_analyst_output]),
            "anti_overfit_analyst",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "anti_overfit_analyst", 5, {"metrics": metrics, "risk_controller": risk_controller_output, "attribution_analyst": attribution_analyst_output}, anti_overfit_analyst_output)
        print(f"    verdict: {anti_overfit_analyst_output.get('verdict', '?')}")
        print(f"    overfit_passed: {anti_overfit_analyst_output.get('overfit_passed', '?')}")

        # 5.4 Backtest Diagnostics (强制执行)
        print("\n  [5.4] Backtest Diagnostics...")
        cooldown = get_cooldown_seconds(base_cooldown, jitter, min_cooldown)
        print(f"    cooldown: {cooldown:.1f}s")
        time.sleep(cooldown)

        backtest_diagnostics_output = retry_agent_spawn(
            lambda: _spawn_agent("backtest_diagnostics", path, strategy_name, current_state, [backtest_result.get("run_log", ""), metrics]),
            "backtest_diagnostics",
            max_retries=max_retries,
        )
        save_agent_record(run_dir, "backtest_diagnostics", 5, {"run_log": backtest_result.get("run_log", ""), "metrics": metrics}, backtest_diagnostics_output)
        print(f"    error_type: {backtest_diagnostics_output.get('error_type', '?')}")
        print(f"    severity: {backtest_diagnostics_output.get('severity', '?')}")

        # Step 6: 提交
        print("\n[Step 6] 提交...")
        verdict = anti_overfit_analyst_output.get("verdict", "discard")
        print(f"  verdict: {verdict}")

        # 更新 results.tsv (覆盖 backtest 写入的 pending 行为,使用最终 verdict)
        # backtest 已经写入了一行 (status=pending),这里更新同一行的 status
        results_path = runs_dir / "results.tsv"
        if results_path.exists():
            content = results_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")
            # 找到最后一个 run_name 行,更新 status
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].startswith(run_name + "\t") or lines[i].startswith(run_name + " "):
                    parts = lines[i].split("\t")
                    if len(parts) >= 12:
                        parts[11] = verdict  # status
                        lines[i] = "\t".join(parts)
                    break
            results_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # 生成并保存 summary.json (Phase 1)
        agent_outputs = {
            "researcher": researcher_output,
            "data_quality": data_quality_output,
            "factor_analyst": factor_analyst_output,
            "strategist": strategist_output,
            "portfolio_construction": portfolio_construction_output,
            "risk_controller": risk_controller_output,
            "attribution_analyst": attribution_analyst_output,
            "anti_overfit_analyst": anti_overfit_analyst_output,
            "backtest_diagnostics": backtest_diagnostics_output,
        }

        # 读取上一轮 summary 用于计算 performance_change
        previous_summary = None
        if round_num > 1:
            prev_run_name = f"run_{(round_num - 1):04d}"
            prev_run_dir = runs_dir / prev_run_name
            if prev_run_dir.exists():
                previous_summary = load_run_summary(prev_run_dir)

        summary = generate_run_summary(
            agent_outputs, metrics, verdict, round_num, previous_summary
        )
        save_run_summary(run_dir, summary)
        print(f"  summary.json 已保存")

        print(f"\n✅ 第 {round_num} 轮完成 ({run_name})")
        print(f"  verdict: {verdict}")
        print(f"  Calmar: {metrics.get('calmar', 'N/A')}")
        print(f"  Sharpe: {metrics.get('sharpe', 'N/A')}")
        print(f"  MaxDD: {metrics.get('max_dd', 'N/A')}")

        # 检查停止条件
        if args.max_rounds and round_num >= args.max_rounds:
            print(f"\n🛑 达到最大轮数 ({args.max_rounds}),停止")
            break

        # 轮间 cooldown
        round_time = time.time() - round_start
        round_cooldown = get_cooldown_seconds(base_cooldown * 2, jitter * 2, min_cooldown * 2)
        if round_time < round_cooldown:
            wait_time = round_cooldown - round_time
            print(f"\n⏳ 轮间 cooldown: {wait_time:.1f}s")
            time.sleep(wait_time)

    return 0


def _spawn_agent(agent_name: str, workspace_path: Path, strategy_name: str,
                 current_state: dict, previous_outputs: list) -> str:
    """spawn 单个 Agent (模拟 Task tool 调用).

    支持通过环境变量 AUTORESEARCH_BEHAVIOR 控制模拟行为:
    - "static": 每次返回相同输出 (默认,用于测试)
    - "varying": 每次返回不同输出 (模拟真实 Agent 探索)
    - "improving": 模拟 Agent 找到改进方案的过程
    """
    import json
    import os
    import random

    behavior = os.environ.get("AUTORESEARCH_BEHAVIOR", "static")
    # 从 current_state 获取轮数
    round_num = current_state.get("total_runs", 0)

    if agent_name == "researcher":
        if behavior == "varying":
            actions = ["search_external", "discover_local", "optimize_param", "remove_factor"]
            directions = ["momentum", "volatility", "value", "quality", "size"]
            idx = round_num % len(actions)
            return json.dumps({
                "action": actions[idx],
                "hypothesis": f"第 {round_num + 1} 轮: 尝试 {directions[idx]} 因子 ({random.randint(1, 100)})",
                "reason": f"基于上一轮结果探索 {directions[idx]} 维度",
                "avoid_actions": ["discover_local"] if round_num > 2 else [],
                "factor_direction": directions[idx],
                "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                              "narrative_bias": "pass", "confirmation_bias": "pass",
                              "recency_bias": "pass"},
            })
        elif behavior == "improving":
            # 模拟 Agent 找到改进方案
            return json.dumps({
                "action": "optimize_param",
                "hypothesis": f"Round {round_num + 1}: 调整 top_n 参数",
                "reason": "降低 top_n 增加集中度",
                "avoid_actions": [],
                "factor_direction": "momentum",
                "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                              "narrative_bias": "pass", "confirmation_bias": "pass",
                              "recency_bias": "pass"},
            })
        return json.dumps({
            "action": "discover_local",
            "hypothesis": "波动率因子可能有效",
            "reason": "当前因子池缺少波动率维度",
            "avoid_actions": [],
            "factor_direction": "volatility",
            "bias_check": {"leader_bias": "pass", "english_bias": "pass",
                          "narrative_bias": "pass", "confirmation_bias": "pass",
                          "recency_bias": "pass"}
        })
    elif agent_name == "data_quality":
        return json.dumps({
            "passed": True,
            "warnings": ["NaN 比例 0.02%"],
            "data_fingerprint": "abc123",
            "nan_ratio": 0.0002,
            "missing_days": 0,
            "price_anomalies": []
        })
    elif agent_name == "factor_analyst":
        if behavior == "varying":
            # 模拟不同轮次返回不同因子
            factors_pool = [
                [{"factor_name": "momentum_60d", "factor_code": "ts_return(close, 60)",
                  "category": "momentum", "ic_mean": 0.045, "ir": 0.62, "overall_score": 0.68, "passed": True}],
                [{"factor_name": "vol_adj_mom", "factor_code": "ts_return(close, 20)/ts_std(return, 20)",
                  "category": "momentum", "ic_mean": 0.052, "ir": 0.71, "overall_score": 0.75, "passed": True}],
                [],
                [{"factor_name": "reversal_10d", "factor_code": "-ts_return(close, 10)",
                  "category": "reversal", "ic_mean": 0.038, "ir": 0.55, "overall_score": 0.62, "passed": True}],
                [],
                [{"factor_name": "momentum_120d", "factor_code": "ts_return(close, 120)",
                  "category": "momentum", "ic_mean": 0.041, "ir": 0.58, "overall_score": 0.66, "passed": True}],
            ]
            candidates = factors_pool[round_num % len(factors_pool)]
            return json.dumps({
                "path_used": "local" if round_num % 2 == 0 else "alpha_zoo",
                "candidates": candidates,
                "rejected": [{"factor_name": f"bad_factor_{round_num}", "reason": "IC < 0.03"}],
                "combination_method": "ic_weighted",
                "recommendation": "建议集成新因子" if candidates else "无有效因子",
            })
        elif behavior == "improving":
            # 在第 3 轮后找到有效因子
            if round_num >= 3:
                return json.dumps({
                    "path_used": "local",
                    "candidates": [{"factor_name": "vol_adj_mom", "factor_code": "ts_return(close, 20)/ts_std(return, 20)",
                                    "category": "momentum", "ic_mean": 0.052, "ir": 0.71,
                                    "overall_score": 0.75, "passed": True}],
                    "rejected": [],
                    "combination_method": "ic_weighted",
                    "recommendation": "建议集成 vol_adj_mom",
                })
            else:
                return json.dumps({
                    "path_used": "local",
                    "candidates": [],
                    "rejected": [{"factor_name": "test", "reason": "IC too low"}],
                    "combination_method": "ic_weighted",
                    "recommendation": "无有效因子",
                })
        return json.dumps({
            "path_used": "local",
            "candidates": [],
            "rejected": [
                {"factor_name": "ts_std_20d", "reason": "IC 0.018 < 0.03"}
            ],
            "combination_method": "ic_weighted",
            "recommendation": "无有效因子"
        })
    elif agent_name == "strategist":
        if behavior == "improving" and round_num >= 3:
            return json.dumps({
                "action": "integrate",
                "changes": [{"param": "FACTOR_EXPRS", "old": [], "new": ["vol_adj_mom"]}],
                "reason": "集成 vol_adj_mom 因子",
                "expected_impact": "Calmar 提升",
            })
        return json.dumps({
            "action": "optimize",
            "changes": [],
            "reason": "无新因子,保持现有策略",
            "expected_impact": "无变化"
        })
    elif agent_name == "portfolio_construction":
        return json.dumps({
            "method": "equal",
            "weights": {},
            "risk_contributions": {},
            "diversification_ratio": 1.0,
            "portfolio_vol": 0.15
        })
    elif agent_name == "risk_controller":
        if behavior == "improving" and round_num >= 3:
            return json.dumps({
                "risk_passed": True,
                "risk_rating": "Green",
                "var_95": -0.018,
                "cvar_95": -0.025,
                "max_drawdown": -0.25,
                "stress_results": {},
                "tail_risk": {"kurtosis": 2.8, "skewness": -0.05}
            })
        return json.dumps({
            "risk_passed": False,
            "risk_rating": "Red",
            "var_95": -0.021,
            "cvar_95": -0.034,
            "max_drawdown": -0.50,
            "stress_results": {},
            "tail_risk": {"kurtosis": 3.2, "skewness": -0.15}
        })
    elif agent_name == "attribution_analyst":
        if behavior == "improving" and round_num >= 3:
            return json.dumps({
                "alpha": 0.005 + round_num * 0.001,
                "beta_mkt": 0.85,
                "beta_smb": 0.05,
                "beta_hml": -0.02,
                "beta_mom": 0.08,
                "sector_allocation": 0.002,
                "stock_selection": 0.003 + round_num * 0.001,
                "interaction": 0.001,
                "bull_capture": 1.05,
                "bear_capture": 0.85,
                "r_squared": 0.90
            })
        return json.dumps({
            "alpha": -0.0039,
            "beta_mkt": 0.92,
            "beta_smb": 0.05,
            "beta_hml": -0.02,
            "beta_mom": 0.08,
            "sector_allocation": 0.001,
            "stock_selection": -0.005,
            "interaction": 0.001,
            "bull_capture": 0.95,
            "bear_capture": 1.12,
            "r_squared": 0.88
        })
    elif agent_name == "anti_overfit_analyst":
        # P0: 基于真实 metrics 计算合理的过拟合检测
        # 从 previous_outputs 提取 metrics (最后一个是 metrics dict)
        metrics = {}
        if previous_outputs:
            last = previous_outputs[-1]
            if isinstance(last, dict):
                metrics = last

        try:
            calmar = float(metrics.get("calmar", 0.0)) if metrics else 0.0
        except (ValueError, TypeError):
            calmar = 0.0
        try:
            sharpe = float(metrics.get("sharpe", 0.0)) if metrics else 0.0
        except (ValueError, TypeError):
            sharpe = 0.0
        try:
            max_dd = float(metrics.get("max_dd", 0.0)) if metrics else 0.0
        except (ValueError, TypeError):
            max_dd = 0.0

        # P2: 加权评分 (start_dependency 权重最高 = 0.20)
        weights = {
            "start_dependency": 0.20,
            "parameter_perturbation": 0.20,
            "rebalance_offset": 0.15,
            "ablation": 0.15,
            "bootstrap": 0.15,
            "monte_carlo": 0.15,
        }

        # P2: pass 阈值 (默认 0.5, 可通过环境变量配置)
        try:
            pass_threshold = float(os.environ.get("ANTI_OVERFIT_THRESHOLD", "0.5"))
        except ValueError:
            pass_threshold = 0.5

        # 基于 metrics 判断每种方法的 pass/fail
        methods_passed = {
            "start_dependency": calmar >= 0.3,                  # Calmar 稳定
            "rebalance_offset": abs(max_dd) <= 0.5,             # 风险可控
            "parameter_perturbation": calmar >= 0.4,             # 参数稳健
            "ablation": calmar > 0.0,                            # 因子有贡献
            "bootstrap": sharpe >= 0.5,                          # 统计显著
            "monte_carlo": calmar >= 0.5 and sharpe >= 0.4,     # 优于随机
        }

        # 计算 weighted_score
        weighted_score = sum(
            weights[k] * (1 if v else 0)
            for k, v in methods_passed.items()
        )

        # 模拟 "improving" 行为 (Round 4+): 所有方法通过
        if behavior == "improving" and round_num >= 4:
            for k in methods_passed:
                methods_passed[k] = True
            weighted_score = 1.0
            analysis = (
                f"所有抗过拟合方法通过 "
                f"(Calmar={calmar:.3f}, Sharpe={sharpe:.3f}, score={weighted_score:.2f})"
            )
        else:
            if weighted_score >= pass_threshold:
                analysis = (
                    f"加权评分通过 "
                    f"({weighted_score:.2f}, Calmar={calmar:.3f}, Sharpe={sharpe:.3f})"
                )
            else:
                failed = [k for k, v in methods_passed.items() if not v]
                analysis = (
                    f"加权评分 {weighted_score:.2f} < {pass_threshold}, "
                    f"失败: {', '.join(failed)}"
                )

        overfit_passed = weighted_score >= pass_threshold
        verdict = "keep" if overfit_passed else "discard"

        return json.dumps({
            "verdict": verdict,
            "overfit_passed": overfit_passed,
            "weighted_score": round(weighted_score, 3),
            "methods_passed": methods_passed,
            "analysis": analysis,
            "suggestions": [] if overfit_passed else ["调整因子参数", "增加训练数据"],
        })
    elif agent_name == "backtest_diagnostics":
        return json.dumps({
            "error_type": "none",
            "severity": "info",
            "symptom": "无异常",
            "root_cause": "N/A",
            "fix_suggestion": "N/A",
            "confidence": 1.0
        })
    else:
        return json.dumps({"error": f"Unknown agent: {agent_name}"})


# ============================================================
# LLM configuration helpers (PR5-c5)
# ============================================================


_LLM_PARENT = argparse.ArgumentParser(
    add_help=False,
    prog="quantnodes-research (LLM flags)",
    description="LLM configuration overrides",
)
_llm_g = _LLM_PARENT.add_argument_group("LLM configuration")
_llm_g.add_argument("--llm-profile", default=None,
                    help="激活的 LLM profile (从 ~/.quantnodes-research/llm.yaml)")
_llm_g.add_argument("--llm-model", default=None, help="覆盖 model")
_llm_g.add_argument("--llm-base-url", default=None, help="覆盖 base_url")
_llm_g.add_argument("--llm-temperature", type=float, default=None,
                    help="覆盖 temperature")
_llm_g.add_argument("--llm-max-tokens", type=int, default=None,
                    help="覆盖 max_tokens")
_llm_g.add_argument("--llm-top-p", type=float, default=None, help="覆盖 top_p")
_llm_g.add_argument("--llm-timeout", type=float, default=None,
                    help="覆盖 timeout_s")
_llm_g.add_argument("--llm-max-retries", type=int, default=None,
                    help="覆盖 max_retries")
_llm_g.add_argument("--llm-seed", type=int, default=None, help="覆盖 seed")
_llm_g.add_argument("--llm-stream", dest="llm_stream",
                    action="store_true", default=None, help="强制流式")
_llm_g.add_argument("--llm-no-stream", dest="llm_stream",
                    action="store_false", help="禁用流式")


def _cli_overrides_from_args(args: argparse.Namespace | None) -> dict:
    """Extract --llm-* kwargs from argparse Namespace."""
    if args is None:
        return {}
    out = {}
    for key, value in vars(args).items():
        if key.startswith("llm_") and value is not None:
            out[key] = value
    return out


def build_llm_config(args: argparse.Namespace | None = None,
                     *, profile: str | None = None,
                     cli_overrides: dict | None = None) -> "LLMConfig":
    """Build an LLMConfig from CLI args + 4-layer merge.

    Args:
        args: argparse Namespace (with --llm-* attributes).
        profile: Explicit profile name override (highest priority).
        cli_overrides: Explicit override dict (alternative to args).

    Returns:
        Fully merged LLMConfig.
    """
    from .core.llm import LLMConfig
    overrides = cli_overrides if cli_overrides is not None else _cli_overrides_from_args(args)
    return LLMConfig.load(profile=profile, cli_overrides=overrides)


def _cmd_llm_list_profiles() -> int:
    """Print all available LLM profiles from yaml config."""
    from .core.llm.config import (
        DEFAULT_LLM_CONFIG_PATH,
        get_default_profile,
        list_profiles,
    )
    profiles = list_profiles()
    default = get_default_profile()
    print(f"# LLM profiles from {DEFAULT_LLM_CONFIG_PATH}")
    if not profiles:
        print("(no llm.yaml found — using code defaults)")
    else:
        for name in profiles:
            marker = " *" if name == default else ""
            print(f"  {name}{marker}")
        print(f"\ndefault: {default}")
    return 0


# ============================================================
# Session commands
# ============================================================

def cmd_webui_serve(args) -> int:
    """启动 Web UI 服务器。"""
    from pathlib import Path

    import uvicorn

    from .api.app import create_app
    from .webui.routes import router as webui_router

    workspace = Path(args.workspace)
    app = create_app(
        workspace_path=workspace if workspace.exists() else None,
        goal_db_path=getattr(args, "goal_db", None),
        hypotheses_path=getattr(args, "hypotheses_path", None),
    )

    # Mount webui routes
    app.include_router(webui_router, tags=["webui"])

    print(f"🌐 Strategy Research Web UI starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=getattr(args, "reload", False),
    )
    return 0


def cmd_api_serve(args) -> int:
    """启动 HTTP API 服务器。"""
    from pathlib import Path

    import uvicorn

    from .api.app import create_app

    workspace = Path(args.workspace)
    app = create_app(
        workspace_path=workspace if workspace.exists() else None,
        goal_db_path=getattr(args, "goal_db", None),
        hypotheses_path=getattr(args, "hypotheses_path", None),
    )

    print(f"🚀 Strategy Research API starting at http://{args.host}:{args.port}")
    print(f"   Workspace: {workspace}")
    print(f"   Docs:      http://{args.host}:{args.port}/docs")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=getattr(args, "reload", False),
    )
    return 0


def cmd_session_stats(args) -> int:
    """查看写入统计。"""
    from .core.session import SessionDB, MetricsLogger
    from .core.session.metrics import MetricsLogger as ML

    db = SessionDB()
    stats = db.metrics_logger.get_stats()

    if not stats or stats.get("total_writes", 0) == 0:
        print("暂无写入记录")
        return 0

    print("=== Session 写入统计 ===")
    print(f"  总写入次数: {stats.get('total_writes', 0)}")
    print(f"  总消息数:   {stats.get('total_messages', 0)}")
    print(f"  成功率:     {stats.get('success_rate', 0):.1%}")
    print(f"  平均速率:   {stats.get('avg_rate', 0):.0f} 条/秒")
    print(f"  最大速率:   {stats.get('max_rate', 0):.0f} 条/秒")
    print(f"  最小速率:   {stats.get('min_rate', 0):.0f} 条/秒")
    print(f"  平均耗时:   {stats.get('avg_duration', 0):.3f}s")
    print(f"  总耗时:     {stats.get('total_duration', 0):.3f}s")

    # 显示最近记录
    recent = db.metrics_logger.get_recent(n=args.recent)
    if recent:
        print(f"\n=== 最近 {len(recent)} 条记录 ===")
        for r in reversed(recent):
            status = "✓" if r["ok"] else "✗"
            print(f"  {status} {r['count']} 条, {r['duration']:.3f}s, {r['rate']:.0f} 条/秒")

    return 0


def cmd_session_list(args) -> int:
    """列出会话。"""
    from .core.session import SessionDB

    db = SessionDB()
    sessions = db.list_sessions(limit=args.limit)

    if not sessions:
        print("暂无会话")
        return 0

    print(f"=== 会话列表 (共 {len(sessions)} 个) ===")
    for s in sessions:
        from datetime import datetime
        created = datetime.fromtimestamp(s.created_at).strftime("%Y-%m-%d %H:%M")
        print(f"  {s.id[:16]:16s}  {created}  {s.workspace or '(global)'}")

    return 0


# ============================================================
# Main CLI
# ============================================================

def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="quantnodes-research",
        description="通用策略自动研究框架",
    )

    # ── Top-level LLM flags (apply globally) ────────────
    parser.add_argument(
        "--llm-list-profiles",
        action="store_true",
        help="列出所有 LLM profile 后退出 (调试用)",
    )

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # init
    init_parser = subparsers.add_parser("init", help="初始化工作区")
    init_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    init_parser.add_argument("--force", action="store_true", help="强制初始化（非空目录）")
    init_parser.add_argument("--no-baseline", action="store_true",
                              help="跳过默认 baseline 回测（更快初始化）")

    # status
    status_parser = subparsers.add_parser("status", help="查看工作区状态")
    status_parser.add_argument("path", nargs="?", default=".", help="工作区路径")

    # reproduce
    reproduce_parser = subparsers.add_parser("reproduce", help="复现实验")
    reproduce_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    reproduce_parser.add_argument("run", nargs="?", help="Run 名称 (例如: run_0001)")
    reproduce_parser.add_argument("--strategy", "-s", help="策略名称")

    # run
    run_parser = subparsers.add_parser("run", parents=[_LLM_PARENT],
                                       help="运行回测")
    run_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    run_parser.add_argument("--strategy", "-s", help="策略名称")
    run_parser.add_argument("--action", "-a", help="行动类型")
    run_parser.add_argument("--description", "-d", help="描述")
    run_parser.add_argument("--timeout", "-t", type=int, default=300, help="超时时间 (秒)")

    # evaluate
    evaluate_parser = subparsers.add_parser("evaluate", parents=[_LLM_PARENT],
                                             help="复跑当前 strategy.py")
    evaluate_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    evaluate_parser.add_argument("--strategy", "-s", help="策略名称")
    evaluate_parser.add_argument("--description", "-d", default="", help="描述")
    evaluate_parser.add_argument("--timeout", "-t", type=int, default=300, help="超时时间 (秒)")

    # preflight
    preflight_parser = subparsers.add_parser("preflight", help="启动前环境检查")
    preflight_parser.add_argument("path", nargs="?", default=".", help="工作区路径")

    # validate
    validate_parser = subparsers.add_parser("validate", help="验证因子")
    validate_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    validate_parser.add_argument("--strategy", "-s", help="策略名称")
    validate_parser.add_argument("--factor", "-f", help="因子表达式")
    validate_parser.add_argument("--source", help="因子来源")

    # list
    list_parser = subparsers.add_parser("list", help="列出实验")
    list_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    list_parser.add_argument("--strategy", "-s", help="策略名称")
    list_parser.add_argument("--limit", "-l", type=int, default=20, help="显示数量")

    # import
    import_parser = subparsers.add_parser("import", help="导入价格数据")
    import_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    import_parser.add_argument("--strategy", "-s", required=True, help="策略名称")
    import_parser.add_argument("--source", required=True,
                               choices=["csv", "parquet", "sample", "tushare", "ifind", "fred", "akshare", "auto"],
                               help="数据源")
    import_parser.add_argument("--file", "-f", help="数据文件路径 (csv/parquet)")
    import_parser.add_argument("--codes", "-c", help="资产代码列表，逗号分隔 (API 数据源)")
    import_parser.add_argument("--start-date", default="2020-01-01", help="开始日期 (API 数据源)")
    import_parser.add_argument("--end-date", default="2025-12-31", help="结束日期 (API 数据源)")
    import_parser.add_argument("--incremental", action="store_true", default=True, help="增量更新 (默认开启)")
    import_parser.add_argument("--no-incremental", dest="incremental", action="store_false", help="全量替换")
    import_parser.add_argument("--date-column", default="date", help="日期列名 (csv)")
    import_parser.add_argument("--price-column", default="close", help="价格列名 (csv)")
    import_parser.add_argument("--asset-column", help="资产代码列名 (csv, 宽格式不需要)")
    import_parser.add_argument("--n-assets", type=int, default=10, help="示例资产数量 (sample)")
    import_parser.add_argument("--n-days", type=int, default=504, help="示例天数 (sample)")

    # autoresearch
    autoresearch_parser = subparsers.add_parser("autoresearch", parents=[_LLM_PARENT],
                                                 help="运行自动化研究循环")
    autoresearch_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    autoresearch_parser.add_argument("--strategy", "-s", help="策略名称")
    autoresearch_parser.add_argument("--cooldown", "-c", type=float, default=30.0, help="基础 cooldown (秒)")
    autoresearch_parser.add_argument("--jitter", "-j", type=float, default=10.0, help="随机抖动范围 (±秒)")
    autoresearch_parser.add_argument("--min-cooldown", type=float, default=1.0, help="最小 cooldown (秒)")
    autoresearch_parser.add_argument("--max-retries", type=int, default=3, help="最大重试次数")
    autoresearch_parser.add_argument("--max-rounds", type=int, help="最大轮数 (不指定则无限循环)")
    autoresearch_parser.add_argument("--lazy-detection-interval", type=int, default=10, help="懒惰检测间隔 (轮数, 默认 10)")
    autoresearch_parser.add_argument("--keep-recent", type=int, default=10, help="读取时保留最近 N 轮详细数据 (其他轮次读取 summary.json, 默认 10)")

    # session
    session_parser = subparsers.add_parser("session", help="会话管理")
    session_subparsers = session_parser.add_subparsers(dest="session_command", help="会话命令")

    # session stats
    session_stats_parser = session_subparsers.add_parser("stats", help="查看写入统计")
    session_stats_parser.add_argument("--recent", "-r", type=int, default=10, help="显示最近 N 条记录")

    # session list
    session_list_parser = session_subparsers.add_parser("list", help="列出会话")
    session_list_parser.add_argument("--limit", "-l", type=int, default=20, help="显示数量")

    # goal (P3-a)
    from .core.goal.cli import add_goal_subparsers
    add_goal_subparsers(subparsers)

    # hypothesis (P3-b)
    from .core.hypothesis.cli import add_hypothesis_subparsers
    add_hypothesis_subparsers(subparsers)

    # validation (P3-c)
    from .core.validation.cli import add_validate_subparsers
    add_validate_subparsers(subparsers)

    from .core.engine.cli import add_engine_subparsers
    add_engine_subparsers(subparsers)

    # api serve
    api_parser = subparsers.add_parser("api", help="HTTP API 服务器")
    api_subparsers = api_parser.add_subparsers(dest="api_command", help="API 命令")
    api_serve_parser = api_subparsers.add_parser("serve", help="启动 API 服务器")
    api_serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    api_serve_parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认 8765)")
    api_serve_parser.add_argument("--reload", action="store_true", help="热重载 (开发模式)")
    api_serve_parser.add_argument("--workspace", "-w", default=".", help="工作区路径")
    api_serve_parser.add_argument("--goal-db", help="Goal DB 路径 (可选)")
    api_serve_parser.add_argument("--hypotheses-path", help="Hypotheses JSON 路径 (可选)")

    # webui serve
    webui_parser = subparsers.add_parser("webui", help="Web UI 仪表盘")
    webui_subparsers = webui_parser.add_subparsers(dest="webui_command", help="Web UI 命令")
    webui_serve_parser = webui_subparsers.add_parser("serve", help="启动 Web UI 服务器")
    webui_serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    webui_serve_parser.add_argument("--port", type=int, default=8766, help="监听端口 (默认 8766)")
    webui_serve_parser.add_argument("--reload", action="store_true", help="热重载 (开发模式)")
    webui_serve_parser.add_argument("--workspace", "-w", default=".", help="工作区路径")
    webui_serve_parser.add_argument("--goal-db", help="Goal DB 路径 (可选)")
    webui_serve_parser.add_argument("--hypotheses-path", help="Hypotheses JSON 路径 (可选)")

    # ── Parse + handle global flags ─────────────────
    args = parser.parse_args()

    # --llm-list-profiles: print and exit early
    if getattr(args, "llm_list_profiles", False):
        return _cmd_llm_list_profiles()

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "reproduce":
        return cmd_reproduce(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "evaluate":
        return cmd_evaluate(args)
    elif args.command == "preflight":
        return cmd_preflight(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "import":
        return cmd_import(args)
    elif args.command == "autoresearch":
        return cmd_autoresearch(args)
    elif args.command == "session":
        if args.session_command == "stats":
            return cmd_session_stats(args)
        elif args.session_command == "list":
            return cmd_session_list(args)
        else:
            session_parser.print_help()
            return 0
    elif args.command == "goal":
        from .core.goal.cli import (
            cmd_goal_audit,
            cmd_goal_cancel,
            cmd_goal_complete,
            cmd_goal_evidence,
            cmd_goal_list,
            cmd_goal_start,
            cmd_goal_status,
        )
        return {
            "start": cmd_goal_start,
            "status": cmd_goal_status,
            "evidence": cmd_goal_evidence,
            "audit": cmd_goal_audit,
            "complete": cmd_goal_complete,
            "list": cmd_goal_list,
            "cancel": cmd_goal_cancel,
        }.get(args.goal_command, lambda a: (goal_parser.print_help(), 0)[1])(args)
    elif args.command == "hypothesis":
        from .core.hypothesis.cli import (
            cmd_hypothesis_create,
            cmd_hypothesis_link,
            cmd_hypothesis_list,
            cmd_hypothesis_search,
            cmd_hypothesis_show,
            cmd_hypothesis_update,
        )
        return {
            "create": cmd_hypothesis_create,
            "list": cmd_hypothesis_list,
            "show": cmd_hypothesis_show,
            "update": cmd_hypothesis_update,
            "search": cmd_hypothesis_search,
            "link": cmd_hypothesis_link,
        }.get(args.hypothesis_command, lambda a: (hypothesis_parser.print_help(), 0)[1])(args)
    elif args.command == "validate-run":
        from .core.validation.cli import cmd_validate_run
        return cmd_validate_run(args)
    elif args.command == "engine":
        from .core.engine.cli import dispatch_engine
        return dispatch_engine(args)
    elif args.command == "api":
        if args.api_command == "serve":
            return cmd_api_serve(args)
        else:
            api_parser.print_help()
            return 0
    elif args.command == "webui":
        if args.webui_command == "serve":
            return cmd_webui_serve(args)
        else:
            webui_parser.print_help()
            return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
