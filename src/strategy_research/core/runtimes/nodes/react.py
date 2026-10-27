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
from dataclasses import dataclass, field
from typing import Any, Callable

from strategy_research.core.agent.strategy.budget import BudgetLimits
from strategy_research.core.runtimes.run_budget import RunBudget

EventSink = Callable[[str, dict], None]


@dataclass
class ReactConfig:
    max_iterations: int = 10
    budget_limits: BudgetLimits | None = None
    system: str | None = None
    # F3：注入式预算（fanout 分支切片优先）；给对象=同一实例直接记账，
    # 传限额只复制上限、消耗回不到父账本（settle 会失真）。
    budget: RunBudget | None = None
    # P5-1b：流式（每轮 LLM 调用逐 chunk 发 text_delta/thinking_delta）
    stream: bool = False


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
    # 注入预算优先（F3）；每次 run 复用同一实例以保证外部可见消耗。
    injected_budget = cfg.budget

    def react_node(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        if cfg.system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": cfg.system}] + messages

        usage = injected_budget or RunBudget(limits=cfg.budget_limits or BudgetLimits())
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

            response = _call_llm(
                client, messages, registry, cfg, run_id, emit,
            )
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


@dataclass
class _AggregatedResponse:
    """流式聚合后的响应，字段形状与非流式响应对齐。"""

    content: str = ""
    usage: dict | None = None
    tool_calls: list = field(default_factory=list)

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class _ToolCallStub:
    """由流式增量还原出的工具调用（供后续 registry.execute 使用）。"""

    id: str
    name: str
    arguments: dict


def _call_llm(
    client: Any,
    messages: list[dict],
    registry: Any,
    cfg: ReactConfig,
    run_id: str,
    emit: Callable[[str, dict], None],
) -> Any:
    """调用 LLM：流式或非流式，返回形状一致的对象。

    流式下 ``delta_tool_calls`` 是增量分片（parser 注释：arguments may span
    multiple chunks），必须按 index 聚合、arguments **字符串拼接**——覆盖会
    静默截断参数（P5-1b 设计 §3.2.1，已用真实 OpenAI 分片验证）。
    """
    if not (cfg.stream and hasattr(client, "stream")):
        return client.chat(messages, tools=registry.get_definitions())

    parts: list[str] = []
    thinking_chars = 0
    usage: dict = {}
    acc: dict[int, dict] = {}
    try:
        for chunk in client.stream(messages, tools=registry.get_definitions()):
            delta = getattr(chunk, "delta_content", "") or ""
            if delta:
                parts.append(delta)
                emit("text_delta", {"run_id": run_id, "delta": delta})
            think = getattr(chunk, "delta_thinking", "") or ""
            if think:
                thinking_chars += len(think)
                emit("thinking_delta", {"run_id": run_id, "delta": think})
            for tc in getattr(chunk, "delta_tool_calls", None) or []:
                idx = tc.get("index", 0)
                slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            chunk_usage = getattr(chunk, "usage", None)
            if isinstance(chunk_usage, dict) and chunk_usage:
                usage = chunk_usage
    except Exception as exc:  # noqa: BLE001 —— 流中断保留已收内容
        emit("stream.failed", {"run_id": run_id, "error": str(exc)})

    if thinking_chars:
        emit("thinking_done", {"run_id": run_id, "chars": thinking_chars})

    tool_calls: list[_ToolCallStub] = []
    for idx in sorted(acc):
        slot = acc[idx]
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["arguments"]) if slot["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            _ToolCallStub(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args)
        )
    return _AggregatedResponse(
        content="".join(parts), usage=usage or None, tool_calls=tool_calls,
    )
