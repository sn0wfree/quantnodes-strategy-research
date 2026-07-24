"""Phase C-1 — AgentLoop-based agent execution.

Replaces the long stub `_spawn_agent(...)` with a real AgentLoop-based factory
that loads `templates/.prompts/<role>.md` as system_prompt and exposes role-
specific tool whitelists.

Falls back to the deterministic stub when:
- `AUTORESEARCH_BEHAVIOR` env var is set (test / CI)
- No LLM API key is configured (LLMConfig has no api_key)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 9 角色对应的 templates/.prompts/<role>.md 文件名
_ROLE_PROMPT_FILES = {
    "researcher": "researcher.md",
    "data_quality": "data_quality.md",
    "factor_analyst": "factor_analyst.md",
    "strategist": "strategist.md",
    "portfolio_construction": "portfolio_construction.md",
    "risk_controller": "risk_controller.md",
    "attribution_analyst": "attribution_analyst.md",
    "anti_overfit_analyst": "anti_overfit_analyst.md",
    "backtest_diagnostics": "backtest_diagnostics.md",
    "critic": "critic.md",
}

# 角色对应的工具白名单 (用 build_default_registry() 注册的 9 个工具名)
_ROLE_TOOL_WHITELIST = {
    "researcher":            ["read_file", "list_history", "factor_analysis", "web_search", "read_url", "get_market_data", "search_symbol"],
    "data_quality":          ["read_file", "web_search", "read_url", "get_market_data", "list_data_sources"],
    "factor_analyst":        ["read_file", "compute_factor", "factor_analysis", "get_market_data"],
    "strategist":            ["read_file", "write_file", "run_backtest", "git_diff", "web_search", "read_url", "get_market_data"],
    "portfolio_construction":["read_file", "get_market_data"],
    "risk_controller":       ["read_file", "factor_analysis", "get_market_data"],
    "attribution_analyst":   ["read_file", "factor_analysis"],
    "anti_overfit_analyst":  ["read_file", "list_history", "factor_analysis"],
    "backtest_diagnostics":  ["read_file", "run_backtest", "git_diff"],
    "critic":                ["read_file", "list_history"],
}


def _prompts_dir() -> Path:
    """Path → templates/.prompts/."""
    from ... import _TEMPLATES_DIR
    return _TEMPLATES_DIR / ".prompts"


def _load_role_system_prompt(role: str) -> str:
    """加载 templates/.prompts/<role>.md 内容, 作为 AgentLoop.system_prompt."""
    prompt_file = _ROLE_PROMPT_FILES.get(role)
    if prompt_file is None:
        return ""
    path = _prompts_dir() / prompt_file
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _get_tool_whitelist(role: str) -> list[str]:
    """返回 role 专属工具白名单. 未知角色返回最小集 (只读)."""
    return _ROLE_TOOL_WHITELIST.get(role, ["read_file"])


def should_use_real_llm() -> bool:
    """判断是否走真 LLM 路径.

    走真 LLM 的条件:
    1. 未设置 AUTORESEARCH_BEHAVIOR (env var 仅用于强制 stub 模式)
    2. LLMConfig 至少有一个可用的 api_key

    Returns:
        True = 走 AgentLoop.run() 真调用
        False = 退到 stub (test / CI / 无 API key)
    """
    if os.environ.get("AUTORESEARCH_BEHAVIOR"):
        return False

    try:
        from ...core.llm import LLMConfig
        cfg = LLMConfig.load()
        # api_key 为空 / 占位 → 不走真 LLM
        if not cfg.api_key or cfg.api_key in ("", "your-api-key-here", "sk-placeholder"):
            return False
        return True
    except Exception:
        return False


def build_agent_loop(
    role: str,
    workspace_path: Path,
    strategy_name: str,
    *,
    llm_config: Any | None = None,
    session_manager: Any | None = None,
    max_iterations: int = 8,
) -> "AgentLoop | None":  # noqa: F821
    """为 role 构造 AgentLoop.

    Args:
        role: 9 角色名 (researcher / strategist / ...)
        workspace_path: 工作区根目录
        strategy_name: 当前策略名 (用于 hypothesis auto-create)
        llm_config: 可选 LLMConfig; None 时自动 LLMConfig.load()
        session_manager: 跨角色共享 session (strategist 可看 researcher 输出)
        max_iterations: ReAct 最大迭代数

    Returns:
        AgentLoop 实例. 如果系统提示词为空, 返回 None (调用方走 stub fallback).
    """
    from ...core.agent.builtin_tools import build_default_registry
    from ...core.agent.loop import AgentLoop
    from ...core.llm import LLMConfig

    system_prompt = _load_role_system_prompt(role)
    if not system_prompt:
        return None

    cfg = llm_config or LLMConfig.load()
    registry = build_default_registry()
    whitelist = _get_tool_whitelist(role)

    return AgentLoop(
        config=cfg,
        registry=registry,
        workspace=workspace_path,
        max_iterations=max_iterations,
        system_prompt=system_prompt,
        allowed_tools=whitelist,
        session_manager=session_manager,
        strategy_name=strategy_name,
        auto_git_commit=False,  # git commit 由 autoresearch 主循环统一控制
    )


def run_agent_via_llm(
    role: str,
    workspace_path: Path,
    strategy_name: str,
    task: str,
    *,
    context: str | None = None,
    previous_outputs: list | None = None,
    llm_config: Any | None = None,
    session_manager: Any | None = None,
    max_iterations: int = 8,
) -> str:
    """调用 AgentLoop.run() 完成 role 任务, 返回 JSON 字符串.

    Args:
        role: 9 角色名
        workspace_path: 工作区根目录
        strategy_name: 策略名
        task: 任务描述 (注入到 system prompt 后的第一条 user message)
        context: 可选上下文 (例如 previous_outputs 序列化的 markdown)
        previous_outputs: 上一步 agent 输出 (用于在 session 中共享)
        llm_config: LLM 配置
        session_manager: 跨调用 session 共享
        max_iterations: ReAct 迭代上限

    Returns:
        AgentLoop.run() 输出的 answer 字段. 期望是合法 JSON 字符串.
        失败时返回 {"error": "..."} JSON 字符串.

    Raises:
        RuntimeError: 如果构造 AgentLoop 失败 (例如 prompt 文件不存在).
    """
    loop = build_agent_loop(
        role=role,
        workspace_path=workspace_path,
        strategy_name=strategy_name,
        llm_config=llm_config,
        session_manager=session_manager,
        max_iterations=max_iterations,
    )
    if loop is None:
        raise RuntimeError(f"无法构造 AgentLoop for role={role!r} (prompt 不存在)")

    # 构造完整任务文本: 上下文 + 之前 agent 输出 + 当前任务
    task_parts = []
    if context:
        task_parts.append(context)
    if previous_outputs:
        task_parts.append("## 之前 Agent 输出 (来自上一阶段)")
        for i, prev in enumerate(previous_outputs, 1):
            if isinstance(prev, dict):
                task_parts.append(f"### 第 {i} 阶段输出:\n```json\n{json.dumps(prev, ensure_ascii=False)}\n```")
            else:
                task_parts.append(f"### 第 {i} 阶段输出:\n```\n{prev}\n```")
    task_parts.append("## 当前任务\n" + task)
    full_task = "\n\n".join(task_parts)

    result = loop.run(full_task)
    if result.error:
        return json.dumps({
            "error": result.error,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
        }, ensure_ascii=False)
    return result.answer


__all__ = [
    "build_agent_loop",
    "run_agent_via_llm",
    "should_use_real_llm",
]
