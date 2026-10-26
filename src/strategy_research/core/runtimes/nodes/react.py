"""react 节点（阶段 1b）—— 工具调用循环。

职责边界（对照 A1 设计「react 节点 = 一次 ReAct 迭代循环」）：
- 有：LLM↔工具循环、预算检查（复用 core/agent/strategy/budget.py，分层整合）、
  工具事件（tool.started/completed）、OpenAI 工具协议消息排序
  （assistant(tool_calls) 后紧跟 role=tool——MiniMax 对乱序返回 400）。
- 无（显式推迟）：memory/goal/hypothesis 注入（ContextHub/A4/B5）、
  mid-loop compaction（A4）、subagent 工具（B1 fanout）、SSE 流式
  （事件经 sink，流式 API 直连留 A1）。

注册表与 client 均注入：react 节点不 import ToolRegistry/工具实现，
单测用假 registry，生产用真实 ToolRegistry（含 30+ 内置工具）。
预算分层：session 层缺省合并（budget_gate）+ run 层三维（budget.py）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from strategy_research.core.agent.strategy.budget import BudgetLimits
from strategy_research.core.runtimes.run_budget import RunBudget

EventSink = Callable[[str, dict], None]


@dataclass
class ReactConfig:
    max_iterations: int = 10
    budget_limits: BudgetLimits | None = None
    system: str | None = None


def make_react_node(
    client: Any,
    registry: Any,  # ToolRegistry（鸭子类型：get_definitions/execute）
    *,
    sink: EventSink | None = None,
    config: ReactConfig | None = None,
) -> Callable[[dict], dict]:
    """构造 react 节点。state 约定同 llm 节点（messages 进出，answer/usage 写回）。"""
    cfg = config or ReactConfig()
    emit = sink or (lambda kind, data: None)

    def react_node(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        if cfg.system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": cfg.system}] + messages

        usage = RunBudget(limits=cfg.budget_limits or BudgetLimits())
        run_id = state.get("run_id", "")
        answer = ""
        stop_reason = "completed"
        iteration = 0

        for iteration in range(1, cfg.max_iterations + 1):
            allowed, reason = usage.plan_call()  # A5 INV-9：唯一预算入口
            if not allowed:
                emit("budget.exhausted", {"run_id": run_id, "reason": reason})
                stop_reason = f"budget:{reason}"
                break

            response = client.chat(messages, tools=registry.get_definitions())
            usage.record(getattr(response, "usage", None))

            if not response.has_tool_calls():
                answer = response.content
                messages.append({"role": "assistant", "content": answer})
                break

            # ── 工具调用分支：assistant(tool_calls) 后紧跟全部 role=tool ──
            tool_calls = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments or {}, ensure_ascii=False)}}
                for tc in response.tool_calls
            ]
            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": tool_calls,
            })

            for tc in response.tool_calls:
                emit("tool.started", {"run_id": run_id, "tool": tc.name, "iteration": iteration})
                result = registry.execute(tc.name, dict(tc.arguments or {}))
                emit(
                    "tool.completed",
                    {"run_id": run_id, "tool": tc.name, "result_chars": len(result)},
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        else:
            stop_reason = f"max_iterations({cfg.max_iterations}) exceeded"
            answer = f"[达到最大迭代次数 {cfg.max_iterations}，任务未完成]"

        updated = dict(state)
        updated["messages"] = messages
        updated["answer"] = answer
        updated["usage"] = {
            **usage.snapshot(),
            "iterations": iteration,
        }
        updated["stop_reason"] = stop_reason
        return updated

    return react_node
