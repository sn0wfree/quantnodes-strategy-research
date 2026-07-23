"""quantnodes-research CLI — 策略研究工作区管理。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from strategy_research import _TEMPLATES_DIR
from .llm_config import _LLM_PARENT, _cli_overrides_from_args, build_llm_config, _cmd_llm_list_profiles
from .commands.autoresearch import cmd_autoresearch, _spawn_agent
from .commands.session import (
    cmd_session_stats, cmd_session_list, cmd_session_show,
    cmd_session_search, cmd_session_delete,
)
from .commands.skills import cmd_skills_list, cmd_skills_show, cmd_skills_search
from .commands.swarm import (
    cmd_swarm_list, cmd_swarm_inspect, cmd_swarm_run, cmd_swarm_cancel,
)
from .commands.server import (
    cmd_webui_serve, cmd_api_serve, cmd_mcp_serve, cmd_mcp_list_tools,
)
from .commands.export import cmd_export


# ============================================================
# 模板内容 (从文件加载或内嵌)
# ============================================================

def _load_template(name: str) -> str:
    """加载模板文件。"""
    template_dir = Path(__file__).parent.parent / "templates"
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
    from strategy_research.core.db import init_db

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
    from strategy_research.core.data_import import generate_sample_data, import_dataframe
    from strategy_research.core.backtest import run_backtest_script

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

    # 复制 templates/.skills/ 下全部 skill (Phase A-2: 全量复制)
    templates_skills = _TEMPLATES_DIR / ".skills"
    copied_skills: list[str] = []
    if templates_skills.is_dir():
        for skill_file in sorted(templates_skills.glob("*.md")):
            content = skill_file.read_text(encoding="utf-8")
            (skills_dir / skill_file.name).write_text(content, encoding="utf-8")
            copied_skills.append(skill_file.name)

    # 若全量复制失败（如 templates 损坏），降级为硬编码子集 (P5 时期的 10 个)
    if not copied_skills:
        for skill_name in [
            "data-routing.md", "factor-research.md", "backtest-diagnose.md",
            "correlation-analysis.md", "ml-strategy.md", "performance-attribution.md",
            "quant-statistics.md", "risk-analysis.md", "sector-rotation.md",
            "research-discipline.md",
        ]:
            skill_content = _load_template(f".skills/{skill_name}")
            if skill_content:
                (skills_dir / skill_name).write_text(skill_content, encoding="utf-8")
                copied_skills.append(skill_name)

    print(f"✓ 创建 .skills/ ({len(copied_skills)} 份方法论)")

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
                table_name = t[0]
                # Use parameterized query with identifier quoting to prevent SQL injection
                count = conn.execute(
                    f"SELECT COUNT(*) FROM \"{table_name}\""
                ).fetchone()[0]
                print(f"  - {table_name}: {count} 行")
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

    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = args.strategy
    if not strategy_name:
        strategy_name = config.get("workspace", {}).get("default_strategy")
        if not strategy_name:
            print("❌ 未指定策略名称，请使用 --strategy <name>")
            return 1

    from strategy_research.core.backtest import run_backtest_script

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
    from strategy_research.core.preflight import run_preflight

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

    # session show
    session_show_parser = session_subparsers.add_parser("show", help="显示会话详情")
    session_show_parser.add_argument("session_id", help="会话 ID")

    # session search
    session_search_parser = session_subparsers.add_parser("search", help="搜索消息")
    session_search_parser.add_argument("query", help="搜索关键词")
    session_search_parser.add_argument("--limit", "-l", type=int, default=20, help="返回数量")

    # session delete
    session_delete_parser = session_subparsers.add_parser("delete", help="删除会话")
    session_delete_parser.add_argument("session_id", help="会话 ID")

    # skills
    skills_parser = subparsers.add_parser("skills", help="技能管理")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", help="技能命令")

    skills_list_parser = skills_subparsers.add_parser("list", help="列出所有技能")
    skills_list_parser.add_argument("--category", "-c", help="按类别筛选")

    skills_show_parser = skills_subparsers.add_parser("show", help="显示技能内容")
    skills_show_parser.add_argument("name", help="技能名称")

    skills_search_parser = skills_subparsers.add_parser("search", help="搜索技能")
    skills_search_parser.add_argument("query", help="搜索关键词")

    # swarm
    swarm_parser = subparsers.add_parser("swarm", help="多智能体协同")
    swarm_subparsers = swarm_parser.add_subparsers(dest="swarm_command", help="swarm 命令")

    swarm_list_parser = swarm_subparsers.add_parser("list", help="列出所有 preset")

    swarm_inspect_parser = swarm_subparsers.add_parser("inspect", help="显示 preset 结构")
    swarm_inspect_parser.add_argument("name", help="preset 名称")

    swarm_run_parser = swarm_subparsers.add_parser("run", help="执行 swarm preset")
    swarm_run_parser.add_argument("name", help="preset 名称")
    swarm_run_parser.add_argument("--workspace", "-w", required=True, help="工作区路径")
    swarm_run_parser.add_argument("--task", "-t", default="", help="任务描述")

    swarm_cancel_parser = swarm_subparsers.add_parser("cancel", help="取消运行中的 swarm")
    swarm_cancel_parser.add_argument("run_id", help="运行 ID")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="MCP 服务器")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", help="MCP 命令")

    mcp_serve_parser = mcp_subparsers.add_parser("serve", help="启动 MCP 服务器")
    mcp_serve_parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", help="传输方式")
    mcp_serve_parser.add_argument("--port", type=int, default=8900, help="SSE 端口")

    mcp_list_tools_parser = mcp_subparsers.add_parser("list-tools", help="列出所有 MCP 工具")

    # export
    export_parser = subparsers.add_parser("export", help="导出策略")
    export_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    export_parser.add_argument("--strategy", "-s", required=True, help="策略名称")
    export_parser.add_argument("--format", "-f", nargs="+", default=["pine", "tdx", "vnpy"],
                               choices=["pine", "tdx", "vnpy"], help="导出格式")
    export_parser.add_argument("--output", "-o", help="输出目录 (默认: workspace/exports/)")

    # schedule (P5)
    from strategy_research.core.scheduled_research.cli import add_schedule_subparsers
    add_schedule_subparsers(subparsers)

    # goal (P3-a)
    from strategy_research.core.goal.cli import add_goal_subparsers
    add_goal_subparsers(subparsers)

    # hypothesis (P3-b)
    from strategy_research.core.hypothesis.cli import add_hypothesis_subparsers
    add_hypothesis_subparsers(subparsers)

    # validation (P3-c)
    from strategy_research.core.validation.cli import add_validate_subparsers
    add_validate_subparsers(subparsers)

    from strategy_research.core.engine.cli import add_engine_subparsers
    add_engine_subparsers(subparsers)

    # strategy acceptance (P6 Step 0) — 离线验收调试工具
    from strategy_research.core.strategy_acceptance.cli import add_accept_subparsers
    add_accept_subparsers(subparsers)

    # portfolio (P3-d) — 组合回测 / 策略相关性
    from strategy_research.core.portfolio.cli import add_portfolio_subparsers
    add_portfolio_subparsers(subparsers)

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
        elif args.session_command == "show":
            return cmd_session_show(args)
        elif args.session_command == "search":
            return cmd_session_search(args)
        elif args.session_command == "delete":
            return cmd_session_delete(args)
        else:
            session_parser.print_help()
            return 0
    elif args.command == "skills":
        if args.skills_command == "list":
            return cmd_skills_list(args)
        elif args.skills_command == "show":
            return cmd_skills_show(args)
        elif args.skills_command == "search":
            return cmd_skills_search(args)
        else:
            skills_parser.print_help()
            return 0
    elif args.command == "swarm":
        if args.swarm_command == "list":
            return cmd_swarm_list(args)
        elif args.swarm_command == "inspect":
            return cmd_swarm_inspect(args)
        elif args.swarm_command == "run":
            return cmd_swarm_run(args)
        elif args.swarm_command == "cancel":
            return cmd_swarm_cancel(args)
        else:
            swarm_parser.print_help()
            return 0
    elif args.command == "mcp":
        if args.mcp_command == "serve":
            return cmd_mcp_serve(args)
        elif args.mcp_command == "list-tools":
            return cmd_mcp_list_tools(args)
        else:
            mcp_parser.print_help()
            return 0
    elif args.command == "export":
        return cmd_export(args)
    elif args.command == "schedule":
        from strategy_research.core.scheduled_research.cli import (
            cmd_schedule_create,
            cmd_schedule_list,
            cmd_schedule_show,
            cmd_schedule_cancel,
            cmd_schedule_delete,
            cmd_schedule_run,
            cmd_schedule_start,
        )
        if args.schedule_command == "create":
            return cmd_schedule_create(args)
        elif args.schedule_command == "list":
            return cmd_schedule_list(args)
        elif args.schedule_command == "show":
            return cmd_schedule_show(args)
        elif args.schedule_command == "cancel":
            return cmd_schedule_cancel(args)
        elif args.schedule_command == "delete":
            return cmd_schedule_delete(args)
        elif args.schedule_command == "run":
            return cmd_schedule_run(args)
        elif args.schedule_command == "start":
            return cmd_schedule_start(args)
        else:
            schedule_parser.print_help()
            return 0
    elif args.command == "goal":
        from strategy_research.core.goal.cli import (
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
        from strategy_research.core.hypothesis.cli import (
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
        from strategy_research.core.validation.cli import cmd_validate_run
        return cmd_validate_run(args)
    elif args.command == "engine":
        from strategy_research.core.engine.cli import dispatch_engine
        return dispatch_engine(args)
    elif args.command == "accept":
        from strategy_research.core.strategy_acceptance.cli import cmd_accept
        return cmd_accept(args)
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
