"""Autoresearch 循环辅助函数。

Main Process = Orchestrator + Main Agent,串行 spawn 每个 Subagent via Task tool。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# 默认配置: 保留最近 N 轮详细数据 (其他轮次优先读取 summary.json)
DEFAULT_KEEP_RECENT = 10


def build_agent_prompt(
    agent_name: str,
    prompts_dir: Path,
    current_state: dict[str, Any],
    previous_outputs: list[dict[str, Any]] | None = None,
) -> str:
    """构造 Agent prompt,用于 Task tool spawn。

    Args:
        agent_name: Agent 名称 (如 "researcher", "data_quality")
        prompts_dir: .prompts/ 目录路径
        current_state: 当前状态 (strategy_py, best_calmar, recent_runs)
        previous_outputs: 之前的 Agent 输出列表

    Returns:
        完整的 prompt 字符串
    """
    # 1. 角色定义 (从 .prompts/*.md 读取)
    prompt_file = prompts_dir / f"{agent_name}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    role_def = prompt_file.read_text(encoding="utf-8")

    # 2. 当前状态
    state_info = f"""
## 当前状态

- strategy.py:
```python
{current_state.get('strategy_py', '')}
```

- 最佳 Calmar: {current_state.get('best_calmar', 0)}
- 当前 Calmar: {current_state.get('current_calmar', 0)}
- 总轮数: {current_state.get('total_runs', 0)}

### 最近 10 轮结果
```
{current_state.get('recent_runs', '')}
```
"""

    # 3. 上一个 Agent 的输出
    prev_output = ""
    if previous_outputs:
        last_output = previous_outputs[-1]
        prev_output = f"""
## 上一个 Agent 的输出

```json
{json.dumps(last_output, indent=2, ensure_ascii=False)}
```
"""

    # 4. 输出格式要求
    output_format = """
## 输出要求

**必须返回纯 JSON,不要包含任何其他文本、解释或 markdown 代码块标记。**

直接以 { 开头,以 } 结尾。

示例格式:
{"key": "value", "number": 123}
"""

    return role_def + state_info + prev_output + output_format


def save_agent_record(
    run_dir: Path,
    agent_name: str,
    step: int,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    duration_ms: int = 0,
) -> Path:
    """保存 Agent 记录到 runs/run_XXXX/agents/。

    Args:
        run_dir: run 目录路径 (如 runs/run_0013/)
        agent_name: Agent 名称
        step: 步骤号 (1-6)
        input_data: 输入数据
        output_data: 输出数据
        duration_ms: 执行耗时 (毫秒)

    Returns:
        保存的文件路径
    """
    agents_dir = run_dir / "agents"
    agents_dir.mkdir(exist_ok=True)

    record = {
        "agent": agent_name,
        "timestamp": datetime.now().isoformat(),
        "step": step,
        "input": input_data,
        "output": output_data,
        "duration_ms": duration_ms,
    }

    filepath = agents_dir / f"{agent_name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return filepath


def read_current_state(
    workspace_path: Path,
    strategy_name: str,
) -> dict[str, Any]:
    """读取当前状态 (strategy.py + results.tsv)。

    Args:
        workspace_path: 工作区路径
        strategy_name: 策略名称

    Returns:
        当前状态字典
    """
    strategy_dir = workspace_path / "strategies" / strategy_name

    # 读取 strategy.py
    strategy_py_path = strategy_dir / "strategy.py"
    strategy_py = strategy_py_path.read_text(encoding="utf-8") if strategy_py_path.exists() else ""

    # 读取 results.tsv
    results_path = strategy_dir / "runs" / "results.tsv"
    lines = []
    if results_path.exists():
        content = results_path.read_text(encoding="utf-8").strip()
        if content:
            lines = content.split("\n")

    header = lines[0] if lines else ""
    recent_runs = "\n".join(lines[-10:]) if len(lines) > 1 else ""

    # 解析最佳 Calmar
    best_calmar = 0.0
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            try:
                calmar = float(parts[3])
                best_calmar = max(best_calmar, calmar)
            except ValueError:
                pass

    return {
        "strategy_py": strategy_py,
        "best_calmar": best_calmar,
        "current_calmar": best_calmar,
        "total_runs": max(len(lines) - 1, 0),
        "recent_runs": recent_runs,
    }


def parse_agent_output(raw_output: str) -> dict[str, Any]:
    """解析 Agent 输出,自动处理 markdown 包裹。

    Args:
        raw_output: Agent 原始输出字符串

    Returns:
        解析后的字典,如果解析失败返回 {"error": "parse_failed", "raw": raw_output}
    """
    if not raw_output or not raw_output.strip():
        return {"error": "empty_output"}

    # 1. 尝试直接 JSON 解析
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        pass

    # 2. 尝试提取 ```json ... ``` 中的内容
    json_match = re.search(r"```json\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 尝试提取 ``` ... ``` 中的内容 (可能是其他格式)
    code_match = re.search(r"```\s*\n?(.*?)\n?\s*```", raw_output, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # 4. 尝试提取 { ... } 或 [ ... ] 中的内容
    brace_match = re.search(r"(\{.*\})", raw_output, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    bracket_match = re.search(r"(\[.*\])", raw_output, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group(1))
        except json.JSONDecodeError:
            pass

    # 5. 所有尝试都失败
    return {"error": "parse_failed", "raw": raw_output[:1000]}


def retry_agent_spawn(
    spawn_fn,
    agent_name: str,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> dict[str, Any]:
    """重试 Agent spawn,直到成功或达到最大重试次数。

    Args:
        spawn_fn: spawn 函数,返回原始字符串
        agent_name: Agent 名称 (用于日志)
        max_retries: 最大重试次数
        retry_delay: 重试间隔 (秒)

    Returns:
        解析后的字典
    """
    for attempt in range(max_retries):
        try:
            raw_output = spawn_fn()
            parsed = parse_agent_output(raw_output)

            # 检查是否解析成功
            if "error" not in parsed:
                return parsed

            # 解析失败,记录警告
            print(f"[autoresearch] {agent_name} 解析失败 (attempt {attempt + 1}/{max_retries}): {parsed.get('error')}")

        except Exception as e:
            print(f"[autoresearch] {agent_name} 执行异常 (attempt {attempt + 1}/{max_retries}): {e}")

        # 等待后重试
        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    # 所有重试都失败
    return {"error": "max_retries_exceeded", "agent": agent_name, "attempts": max_retries}


def get_cooldown_seconds(base_cooldown: float = 30.0, jitter: float = 10.0, min_cooldown: float = 1.0) -> float:
    """计算带随机抖动的 cooldown 时间。

    Args:
        base_cooldown: 基础 cooldown (秒)
        jitter: 随机抖动范围 (±秒)
        min_cooldown: 最小 cooldown (秒)

    Returns:
        实际 cooldown 时间 (秒)
    """
    import random
    actual = base_cooldown + random.uniform(-jitter, jitter)
    return max(min_cooldown, actual)


# ============================================================
# Lazy Detection (懒惰检测)
# ============================================================

def should_run_lazy_detection(round_num: int, interval: int = 10) -> bool:
    """判断是否应该运行懒惰检测。

    Args:
        round_num: 当前轮数
        interval: 检测间隔 (默认 10 轮)

    Returns:
        是否应该运行检测
    """
    return round_num > 0 and round_num % interval == 0


def read_agent_history(
    runs_dir: Path,
    agent_name: str,
    threshold: int = 10,
    current_round: int | None = None,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> list[dict[str, Any]]:
    """读取最近 N 轮的 Agent 记录 (按需加载摘要)。

    最近 keep_recent 轮: 读取详细 agents/*.json
    超过 keep_recent 轮: 读取 summary.json 中的 agent_summaries.{name}

    注意: 物理文件全部保留, 只是读取策略不同。

    Args:
        runs_dir: runs 目录路径
        agent_name: Agent 名称
        threshold: 读取最近 N 轮 (默认 10)
        current_round: 当前轮数 (用于判断是否读取详细)
        keep_recent: 保留详细数据的最近轮数 (默认 10)

    Returns:
        历史记录列表 [{"round": N, "output": {...}, "source": "detailed|summary"}, ...]
    """
    history = []

    # 获取所有 run 目录 (排序)
    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda d: d.name
    )

    # 只读取最近 threshold 轮
    for run_dir in run_dirs[-threshold:]:
        run_name = run_dir.name
        try:
            round_num = int(run_name.split("_")[1]) if "_" in run_name else 0
        except (ValueError, IndexError):
            continue

        # 决定读取源
        read_detailed = (
            current_round is None
            or should_read_detailed(round_num, current_round, keep_recent)
        )

        if read_detailed:
            # 读取详细 agents/*.json
            agent_file = run_dir / "agents" / f"{agent_name}.json"
            if agent_file.exists():
                try:
                    with open(agent_file, "r", encoding="utf-8") as f:
                        record = json.load(f)
                    history.append({
                        "round": round_num,
                        "output": record.get("output", {}),
                        "timestamp": record.get("timestamp", ""),
                        "source": "detailed",
                    })
                    continue
                except (json.JSONDecodeError, KeyError, OSError):
                    pass

        # 读取 summary.json (摘要)
        summary = load_run_summary(run_dir)
        if summary and "agent_summaries" in summary:
            agent_summary = summary["agent_summaries"].get(agent_name, "")
            history.append({
                "round": round_num,
                "output": {
                    "summary": agent_summary,
                    "verdict": summary.get("verdict"),
                    "metrics": summary.get("metrics", {}),
                },
                "timestamp": summary.get("timestamp", ""),
                "source": "summary",
            })

    return history


def detect_lazy_behavior(
    agent_name: str,
    current_output: dict[str, Any],
    history: list[dict[str, Any]],
    threshold: int = 3,
) -> dict[str, Any]:
    """检测 Agent 是否在偷懒。

    Args:
        agent_name: Agent 名称
        current_output: 当前输出
        history: 历史记录 (最近 N 轮)
        threshold: 重复阈值 (默认 3)

    Returns:
        {"lazy_score": float, "issues": list[str], "is_lazy": bool}
    """
    lazy_score = 0.0
    issues = []

    if not history:
        return {"lazy_score": 0.0, "issues": [], "is_lazy": False}

    recent_outputs = [h.get("output", {}) for h in history[-threshold:]]

    if agent_name == "researcher":
        # 检查 hypothesis 是否重复
        recent_hypotheses = [h.get("hypothesis") for h in recent_outputs if h.get("hypothesis")]
        if current_output.get("hypothesis") in recent_hypotheses:
            lazy_score += 0.5
            issues.append("hypothesis 与上轮相同")

        # 检查 action 是否重复
        recent_actions = [h.get("action") for h in recent_outputs if h.get("action")]
        if current_output.get("action") in recent_actions:
            lazy_score += 0.3
            issues.append("action 与上轮相同")

    elif agent_name == "factor_analyst":
        # 检查 candidates 是否连续为空
        recent_candidates = [h.get("candidates", []) for h in recent_outputs]
        if all(len(c) == 0 for c in recent_candidates) and len(recent_candidates) >= threshold:
            lazy_score += 0.3
            issues.append(f"连续 {threshold} 轮无候选因子")

        # 检查 rejected 因子是否相同
        recent_rejected_names = [
            [r.get("factor_name") for r in h.get("rejected", [])]
            for h in recent_outputs
        ]
        current_rejected_names = [r.get("factor_name") for r in current_output.get("rejected", [])]
        if recent_rejected_names and current_rejected_names:
            if all(set(current_rejected_names) == set(r) for r in recent_rejected_names):
                lazy_score += 0.2
                issues.append("rejected 因子与上轮相同")

    elif agent_name == "strategist":
        # 检查 changes 是否连续为空
        recent_changes = [h.get("changes", []) for h in recent_outputs]
        if all(len(c) == 0 for c in recent_changes) and len(recent_changes) >= threshold:
            lazy_score += 0.4
            issues.append(f"连续 {threshold} 轮无 changes")

        # 检查 action 是否连续相同
        recent_actions = [h.get("action") for h in recent_outputs if h.get("action")]
        if recent_actions and all(a == recent_actions[0] for a in recent_actions):
            lazy_score += 0.3
            issues.append("action 连续相同")

    elif agent_name == "risk_controller":
        # 检查 risk_rating 是否连续相同
        recent_ratings = [h.get("risk_rating") for h in recent_outputs if h.get("risk_rating")]
        if recent_ratings and all(r == recent_ratings[0] for r in recent_ratings):
            lazy_score += 0.2
            issues.append("risk_rating 连续相同")

    elif agent_name == "anti_overfit_analyst":
        # 检查 verdict 是否连续 discard
        recent_verdicts = [h.get("verdict") for h in recent_outputs if h.get("verdict")]
        if recent_verdicts and all(v == "discard" for v in recent_verdicts):
            lazy_score += 0.4
            issues.append(f"连续 {len(recent_verdicts)} 轮 verdict=discard")

        # 检查 overfit_passed 是否连续 false
        recent_overfit = [h.get("overfit_passed") for h in recent_outputs if "overfit_passed" in h]
        if recent_overfit and all(v == False for v in recent_overfit):
            lazy_score += 0.3
            issues.append("overfit_passed 连续 false")

    return {
        "lazy_score": min(lazy_score, 1.0),
        "issues": issues,
        "is_lazy": lazy_score >= 0.3,
    }


def save_laziness_report(
    run_dir: Path,
    round_num: int,
    lazy_results: list[dict[str, Any]],
    overall_lazy_score: float,
) -> Path:
    """保存 laziness report 到 runs/run_XXXX/。

    Args:
        run_dir: run 目录路径
        round_num: 当前轮数
        lazy_results: 检测结果列表
        overall_lazy_score: 整体懒惰分数

    Returns:
        保存的文件路径
    """
    # 生成 summary
    lazy_agents = [r for r in lazy_results if r.get("issues")]
    if lazy_agents:
        agent_names = [r["agent"] for r in lazy_agents]
        summary = f"{'、'.join(agent_names)} 存在懒惰行为"
    else:
        summary = "所有 Agent 行为正常"

    report = {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "overall_lazy_score": overall_lazy_score,
        "agents": lazy_results,
        "summary": summary,
    }

    filepath = run_dir / "laziness_report.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return filepath


# ============================================================
# Summary Generation (Phase 1)
# ============================================================

def _extract_actions(agent_outputs: dict[str, dict]) -> list[str]:
    """从 agent outputs 提取 actions 列表。

    Args:
        agent_outputs: {"agent_name": output_dict}

    Returns:
        actions 列表 (e.g., ["researcher: search_external", "change: top_n=5"])
    """
    actions = []

    researcher = agent_outputs.get("researcher", {})
    if researcher.get("action"):
        actions.append(f"researcher: {researcher['action']}")

    strategist = agent_outputs.get("strategist", {})
    if strategist.get("action"):
        actions.append(f"strategist: {strategist['action']}")

    for change in strategist.get("changes", []):
        actions.append(f"change: {change.get('param', '?')}={change.get('new', '?')}")

    return actions


def _summarize_each_agent(agent_outputs: dict[str, dict]) -> dict[str, str]:
    """每个 Agent 一句话总结 (~50-100 chars)。

    Args:
        agent_outputs: {"agent_name": output_dict}

    Returns:
        {"agent_name": "summary_text"}
    """
    summaries = {}

    # Researcher
    researcher = agent_outputs.get("researcher", {})
    if researcher:
        action = researcher.get("action", "?")
        direction = researcher.get("factor_direction", "")
        hypothesis = researcher.get("hypothesis", "")[:40]
        summaries["researcher"] = f"{action} | {direction} | {hypothesis}"[:100]

    # Data Quality
    data_quality = agent_outputs.get("data_quality", {})
    if data_quality:
        passed = data_quality.get("passed", False)
        warnings_count = len(data_quality.get("warnings", []))
        summaries["data_quality"] = f"passed={passed} | warnings={warnings_count}"

    # Factor Analyst
    factor = agent_outputs.get("factor_analyst", {})
    if factor:
        candidates = factor.get("candidates", [])
        rec = factor.get("recommendation", "")[:30]
        summaries["factor_analyst"] = f"{len(candidates)} candidates | {rec}"

    # Strategist
    strategist = agent_outputs.get("strategist", {})
    if strategist:
        changes = strategist.get("changes", [])
        action = strategist.get("action", "?")
        summaries["strategist"] = f"{action} | {len(changes)} changes"

    # Portfolio Construction
    portfolio = agent_outputs.get("portfolio_construction", {})
    if portfolio:
        method = portfolio.get("method", "?")
        vol = portfolio.get("portfolio_vol", 0)
        summaries["portfolio_construction"] = f"{method} | vol={vol:.3f}"[:80]

    # Risk Controller
    risk = agent_outputs.get("risk_controller", {})
    if risk:
        passed = risk.get("risk_passed", False)
        rating = risk.get("risk_rating", "?")
        summaries["risk_controller"] = f"passed={passed} | rating={rating}"

    # Attribution Analyst
    attribution = agent_outputs.get("attribution_analyst", {})
    if attribution:
        alpha = attribution.get("alpha", 0)
        beta = attribution.get("beta_mkt", 0)
        summaries["attribution_analyst"] = f"alpha={alpha:.4f} | beta={beta:.2f}"

    # Anti-overfit Analyst
    anti_overfit = agent_outputs.get("anti_overfit_analyst", {})
    if anti_overfit:
        verdict = anti_overfit.get("verdict", "?")
        passed = anti_overfit.get("overfit_passed", False)
        summaries["anti_overfit_analyst"] = f"{verdict} | overfit_passed={passed}"

    # Backtest Diagnostics
    diagnostics = agent_outputs.get("backtest_diagnostics", {})
    if diagnostics:
        error_type = diagnostics.get("error_type", "?")
        severity = diagnostics.get("severity", "?")
        summaries["backtest_diagnostics"] = f"error={error_type} | severity={severity}"

    return summaries


def _extract_key_insight(
    agent_outputs: dict[str, dict],
    metrics: dict[str, Any],
) -> str:
    """提取本轮关键洞察 (~100-150 chars)。

    Args:
        agent_outputs: 所有 agent 输出
        metrics: 本轮 metrics

    Returns:
        关键洞察字符串
    """
    insights = []

    # 从 anti_overfit 提取
    anti_overfit = agent_outputs.get("anti_overfit_analyst", {})
    analysis = anti_overfit.get("analysis", "")
    if analysis:
        insights.append(analysis[:80])

    # 从 attribution 提取
    attribution = agent_outputs.get("attribution_analyst", {})
    alpha = attribution.get("alpha", 0)
    if alpha:
        insights.append(f"alpha={alpha:.3f}")

    # 从 researcher 提取
    researcher = agent_outputs.get("researcher", {})
    reason = researcher.get("reason", "")
    if reason:
        insights.append(f"reason: {reason[:50]}")

    return " | ".join(insights)[:150] if insights else ""


def _compute_performance_change(
    current_metrics: dict[str, Any],
    previous_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算本轮相对于上轮的变化。

    Args:
        current_metrics: 本轮 metrics
        previous_summary: 上轮的 summary (从 summary.json 读取)

    Returns:
        {
            "calmar_delta": float,
            "sharpe_delta": float,
            "max_dd_delta": float,
            "verdict_changed": bool,
        }
    """
    if not previous_summary:
        return {}

    prev_metrics = previous_summary.get("metrics", {})
    prev_verdict = previous_summary.get("verdict", "")

    current_verdict = current_metrics.get("verdict", "")

    return {
        "calmar_delta": current_metrics.get("calmar", 0) - prev_metrics.get("calmar", 0),
        "sharpe_delta": current_metrics.get("sharpe", 0) - prev_metrics.get("sharpe", 0),
        "max_dd_delta": current_metrics.get("max_dd", 0) - prev_metrics.get("max_dd", 0),
        "verdict_changed": prev_verdict != current_verdict,
    }


def generate_run_summary(
    agent_outputs: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    verdict: str,
    round_num: int,
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成 run 摘要 (~300 chars)。

    借鉴 OpenCode CompactionPart 的设计,提供快速访问层而不删除原始 agents/。

    Args:
        agent_outputs: 所有 agent 的输出 {"agent_name": output_dict}
        metrics: 本轮 metrics (calmar, sharpe, max_dd, ann_return, ann_vol, turnover)
        verdict: 本轮 verdict (keep/discard)
        round_num: 当前轮数
        previous_summary: 上轮的 summary (用于计算 performance_change)

    Returns:
        summary dict (~300 chars):
        {
            "round": int,
            "timestamp": str,
            "verdict": "keep|discard",
            "metrics": {...},
            "actions": [...],
            "agent_summaries": {...},
            "hypothesis": str,
            "key_insight": str,
            "performance_change": {...},
        }
    """
    summary = {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "verdict": verdict,
        "metrics": {
            "calmar": metrics.get("calmar", 0),
            "sharpe": metrics.get("sharpe", 0),
            "max_dd": metrics.get("max_dd", 0),
            "ann_return": metrics.get("ann_return", 0),
            "ann_vol": metrics.get("ann_vol", 0),
            "turnover": metrics.get("turnover", 0),
        },
        "actions": _extract_actions(agent_outputs),
        "agent_summaries": _summarize_each_agent(agent_outputs),
        "hypothesis": agent_outputs.get("researcher", {}).get("hypothesis", ""),
        "key_insight": _extract_key_insight(agent_outputs, metrics),
    }

    # 计算 performance_change (需要包含 verdict 用于对比)
    metrics_with_verdict = dict(metrics)
    metrics_with_verdict["verdict"] = verdict
    summary["performance_change"] = _compute_performance_change(
        metrics_with_verdict, previous_summary
    )

    return summary


def save_run_summary(run_dir: Path, summary: dict[str, Any]) -> Path:
    """保存 summary.json 到 run_dir。

    Args:
        run_dir: run 目录路径 (e.g., runs/run_0042/)
        summary: generate_run_summary() 返回的 dict

    Returns:
        保存的文件路径
    """
    summary_file = run_dir / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary_file


def load_run_summary(run_dir: Path) -> dict[str, Any] | None:
    """快速加载 summary.json (~300 chars)。

    Args:
        run_dir: run 目录路径

    Returns:
        summary dict 或 None (如果不存在)
    """
    summary_file = run_dir / "summary.json"
    if summary_file.exists():
        try:
            return json.loads(summary_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


# ============================================================
# Layered Read Configuration (Phase 2)
# ============================================================

# DEFAULT_KEEP_RECENT 已在文件顶部定义


def should_read_detailed(
    round_num: int,
    current_round: int,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> bool:
    """判断 run 是否读取详细数据 (按距当前轮的距离)。

    Args:
        round_num: run 轮数
        current_round: 当前轮数
        keep_recent: 保留详细数据的最近轮数 (默认 10)

    Returns:
        True = 读取详细 agents/*.json
        False = 读取 summary.json
    """
    return current_round - round_num < keep_recent


def get_run_data(
    run_dir: Path,
    current_round: int,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> dict[str, Any]:
    """根据距当前轮数决定读取详细还是摘要。

    注意: 不删除任何文件, 只是选择读取源。物理文件全部保留。

    Args:
        run_dir: run 目录路径
        current_round: 当前轮数
        keep_recent: 保留详细数据的轮数

    Returns:
        {
            "detailed": bool,    # 是否为详细数据
            "source": str,       # "agents" | "summary" | "tsv" | "none"
            "data": dict,        # 完整 agents 或 summary 或 tsv 行
            "round": int,        # 轮数
        }
    """
    try:
        round_num = int(run_dir.name.split("_")[1])
    except (ValueError, IndexError):
        return {"detailed": False, "source": "none", "data": {}, "round": 0}

    if should_read_detailed(round_num, current_round, keep_recent):
        # 最近 keep_recent 轮: 读取详细
        agents_dir = run_dir / "agents"
        if agents_dir.exists():
            return {
                "detailed": True,
                "source": "agents",
                "data": _load_all_agents(agents_dir),
                "round": round_num,
            }

    # 超过 keep_recent 轮: 读取 summary
    summary = load_run_summary(run_dir)
    if summary:
        return {
            "detailed": False,
            "source": "summary",
            "data": summary,
            "round": round_num,
        }

    # 无 summary: 降级到 None (保留物理 agents/ 但不读取)
    return {
        "detailed": False,
        "source": "none",
        "data": {},
        "round": round_num,
    }


def _load_all_agents(agents_dir: Path) -> dict[str, dict[str, Any]]:
    """加载所有 agent 记录。

    Args:
        agents_dir: agents/ 目录路径

    Returns:
        {"agent_name": output_dict}
    """
    result = {}
    for agent_file in agents_dir.glob("*.json"):
        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))
            result[agent_file.stem] = data.get("output", {})
        except (json.JSONDecodeError, OSError):
            pass
    return result
