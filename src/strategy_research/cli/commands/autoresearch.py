"""Extracted from cli.py — autoresearch command and agent spawning logic.

Contains:
- cmd_autoresearch: automated research loop
- _spawn_agent: spawn a single agent (stub or real LLM)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import yaml


def cmd_autoresearch(args: argparse.Namespace) -> int:
    """执行 autoresearch 命令 - 运行自动化研究循环。"""
    from strategy_research.core.autoresearch import (
        build_agent_prompt, save_agent_record, read_current_state,
        parse_agent_output, retry_agent_spawn, get_cooldown_seconds,
        should_run_lazy_detection, read_agent_history, detect_lazy_behavior, save_laziness_report,
        generate_run_summary, save_run_summary, load_run_summary, DEFAULT_KEEP_RECENT,
    )
    from strategy_research.core.backtest import run_backtest_script

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

        # Phase C-2: 用 decide() 替代内嵌 verdict 逻辑
        # 1) 从 anti_overfit_analyst_output 提取 llm_verdict (如有)
        aoa_llm_verdict = None
        if isinstance(anti_overfit_analyst_output, dict):
            aoa_llm_verdict = {
                "passed": bool(anti_overfit_analyst_output.get("overfit_passed", False)),
                "score": float(anti_overfit_analyst_output.get("overfit_score", 0.5) or 0.5),
                "reason": anti_overfit_analyst_output.get("verdict_reason", ""),
                "concerns": anti_overfit_analyst_output.get("methods_passed", []),
                "source": "anti_overfit_analyst",
            }

        # 2) 调用 decide() (传入 stagnation_count 让连续 reject 自动停止)
        from strategy_research.core.strategy_acceptance import (
            AcceptanceConfig,
            decide as make_decision,
            load_config as load_acceptance_config,
        )
        acceptance_cfg = load_acceptance_config(
            workspace_config=path / "acceptance.yaml",
        )

        decision = make_decision(
            metrics=metrics,
            llm_verdict=aoa_llm_verdict,
            cfg=acceptance_cfg,
            stagnation_count=int(anti_overfit_analyst_output.get("stagnation_count", 0) or 0)
                if isinstance(anti_overfit_analyst_output, dict) else 0,
        )

        verdict = "keep" if decision.accept else "discard"
        print(f"  decision.accept: {decision.accept}")
        print(f"  decision.reason: {decision.reason}")
        print(f"  decision.stagnation_triggered: {decision.stagnation_triggered}")
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
        # Phase C-2: 把 decision breakdown 写入 summary (供审计)
        summary["acceptance_decision"] = decision.to_dict()
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

        # Phase C-2: stagnation stop — 连续 N 轮 reject 触发自动停止
        if decision.stagnation_triggered:
            print(f"\n🛑 stagnation 触发 ({decision.reason}), 停止 autoresearch")
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
    """spawn 单个 Agent (Phase C-1: 真接 AgentLoop + 角色 prompt).

    决策:
    - 满足条件时 (有 LLM API key 且未设 AUTORESEARCH_BEHAVIOR) → 真调 AgentLoop.run()
    - 否则退到 stub (test / CI / 无 API key)

    stub 行为由环境变量 AUTORESEARCH_BEHAVIOR 控制:
    - "static": 每次返回相同输出 (默认,用于测试)
    - "varying": 每次返回不同输出 (模拟真实 Agent 探索)
    - "improving": 模拟 Agent 找到改进方案的过程

    返回: JSON 字符串, 调用方 parse_agent_output() 解码.
    """
    from strategy_research.core.agent.role_factory import (
        should_use_real_llm,
        run_agent_via_llm,
        build_agent_loop,
    )

    # Phase C-1: 真 LLM 路径
    if should_use_real_llm():
        try:
            # 构造任务: 综合 current_state + previous_outputs
            task_lines = [f"你是 {agent_name}. 你的工作目录: {workspace_path}"]
            if current_state:
                task_lines.append("当前状态:")
                task_lines.append(json.dumps(current_state, ensure_ascii=False, default=str))
            return run_agent_via_llm(
                role=agent_name,
                workspace_path=workspace_path,
                strategy_name=strategy_name,
                task="\n".join(task_lines),
                previous_outputs=previous_outputs,
                max_iterations=8,
            )
        except Exception as exc:
            # 真 LLM 失败 → 退到 stub, 不让主循环崩
            print(f"⚠️  AgentLoop.run() 失败 ({agent_name}): {exc}; 退到 stub")

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
    elif agent_name == "critic":
        # Phase A-3: 评审 / 风险门禁 — 把现有 critic.md prompt 接到 stub
        if behavior == "improving" and round_num >= 2:
            approved = True
        else:
            approved = round_num >= 1
        return json.dumps({
            "approved": approved,
            "risk_rating": "low" if approved else "high",
            "concerns": [] if approved else ["过度拟合", "样本外未验证"],
            "suggested_fixes": [] if approved else ["延长样本", "加入 walk-forward 验证"],
            "confidence": 0.7 if approved else 0.4,
            "review_dimensions": {
                "risk": "pass" if approved else "fail",
                "attribution": "pass",
                "diagnostics": "pass" if approved else "fail",
                "statistics": "pass",
            },
        })
    else:
        return json.dumps({"error": f"Unknown agent: {agent_name}"})
