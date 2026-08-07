"""SubAgentTool — delegate focused subtasks to a lightweight sub-agent.

Constraints (enforced):
    1. Sub-agents cannot call SubAgentTool (no nested delegation).
    2. Parent agent may spawn at most MAX_SUBAGENTS per turn.
       Excess calls return an actionable error.

The sub-agent runs via SwarmWorker (lightweight ReAct loop) and returns
the answer as a tool result to the parent agent.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ..tools import BaseTool, ToolRegistry
from ...llm.config import LLMConfig
from ...llm.openai_client import OpenAICompatClient

logger = logging.getLogger(__name__)

MAX_SUBAGENTS = 5


def _forward_event(
    emit_event: Any,
    agent_id: str,
    message_id: str | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Forward a child agent event as a subagent_* SSE event."""
    if emit_event is None:
        return
    mapping = {
        "tool_call": "subagent_tool_call",
        "tool_result": "subagent_tool_result",
        "text_delta": "subagent_text_delta",
    }
    mapped = mapping.get(event_type)
    if mapped:
        emit_event(mapped, {
            "agent_id": agent_id,
            "message_id": message_id,
            **data,
        })


class SubAgentTool(BaseTool):
    """委派子任务给专注的子 agent，获取结果后继续。

    子 agent 运行一个轻量的 ReAct 循环（SwarmWorker），拥有独立的
    工具白名单（不含 SubAgentTool 自身），可读文件、跑回测、调用因子
    分析等工具。结果作为工具返回值交回父 agent。

    约束:
    - 子 agent 不能再次调用 SubAgentTool（禁止嵌套委派）。
    - 父 agent 单次执行最多委派 MAX_SUBAGENTS 个子任务。
    """

    name = "delegate_to_agent"
    category = "agent"
    description = (
        "委派一个独立的子任务给专注的子 agent。子 agent 拥有独立的推理循环，"
        "可读写文件、运行回测、调用因子分析等工具。适合需要独立思考或多步操作"
        "的子任务。结果会作为文本返回。"
    )
    parameters = {
        "task": {
            "type": "string",
            "description": (
                "子任务的详细描述。应包含足够的上下文让子 agent 独立完成工作，"
                "例如目标、约束条件、期望的输出格式等。"
            ),
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "可选的工具白名单。如果指定，子 agent 只能使用这些工具。"
                "不指定则使用全部可用工具（不含 delegate_to_agent）。"
            ),
        },
        "max_iterations": {
            "type": "integer",
            "description": "子 agent 最大推理轮次（默认 10，最大 20）。",
            "default": 10,
        },
    }

    def execute(self, **kwargs: Any) -> str:
        """Execute delegation — runs a SwarmWorker in a thread."""
        # Extract params
        task = kwargs.get("task", "")
        if not task:
            return json.dumps(
                {"status": "error", "error": "missing required parameter 'task'"},
                ensure_ascii=False,
            )

        tools_whitelist: list[str] | None = kwargs.get("tools")
        max_iterations = min(kwargs.get("max_iterations", 10), 20)

        # Emit event helpers
        emit_event = kwargs.get("emit_event")
        message_id = kwargs.get("message_id")
        workspace = kwargs.get("workspace")
        subagent_count_ref = kwargs.get("_subagent_count_ref")

        # ── Count limit ────────────────────────────────────────────
        if subagent_count_ref is not None:
            current = subagent_count_ref[0]
            if current >= MAX_SUBAGENTS:
                return json.dumps(
                    {
                        "status": "error",
                        "error": (
                            f"单次最多委派 {MAX_SUBAGENTS} 个子任务（已达上限）。"
                            "更多任务请使用工作流执行（/study 命令），"
                            "或减少委派数量。"
                        ),
                    },
                    ensure_ascii=False,
                )
            subagent_count_ref[0] = current + 1

        agent_id = f"sub-{uuid.uuid4().hex[:8]}"

        # ── Emit subagent_started ──────────────────────────────────
        _forward_event(emit_event, agent_id, message_id, "subagent_started", {
            "agent_id": agent_id,
            "name": task[:80],
            "message_id": message_id,
        })

        # ── Build child SwarmWorker ────────────────────────────────
        try:
            from ...workflow.worker import SwarmWorker
            from ..tools import ToolRegistry as TR

            config = LLMConfig.load()
            client = OpenAICompatClient(config)

            # Build filtered registry (exclude delegate_to_agent)
            parent_registry: ToolRegistry | None = kwargs.get("_parent_registry")
            if parent_registry is not None:
                filtered = TR()
                for t_name, t_obj in parent_registry._tools.items():
                    if t_name != "delegate_to_agent":
                        filtered.register(t_obj)
            else:
                filtered = TR()

            # Apply explicit whitelist if provided
            if tools_whitelist:
                wl_registry = TR()
                for t_name in tools_whitelist:
                    t = filtered.get(t_name)
                    if t is not None:
                        wl_registry.register(t)
                filtered = wl_registry

            # System prompt for sub-agent
            system_prompt = (
                "你是一个专注的子 agent。根据给定的任务独立完成工作，"
                "使用可用工具读取数据、运行分析、生成报告。\n"
                "回复简洁，直接给出结果。"
            )

            worker = SwarmWorker(
                client=client,
                registry=filtered,
                system_prompt=system_prompt,
                max_iterations=max_iterations,
                timeout_s=120.0,
            )

            # Set event callback for forwarding
            worker.set_event_callback(
                lambda et, data: _forward_event(
                    emit_event, agent_id, message_id, et, data,
                ),
            )

            # ── Run (sync — execute() is already in a thread via to_thread) ─
            result = worker.run(task)

            # ── Emit subagent_completed ────────────────────────────
            _forward_event(emit_event, agent_id, message_id, "subagent_completed", {
                "agent_id": agent_id,
                "message_id": message_id,
            })

            return json.dumps(
                {
                    "status": "ok",
                    "answer": result.answer,
                    "summary": result.summary,
                    "iterations": result.iterations,
                    "tool_calls_made": result.tool_calls_made,
                },
                ensure_ascii=False,
            )

        except Exception as exc:
            logger.exception("SubAgentTool failed")
            _forward_event(emit_event, agent_id, message_id, "subagent_failed", {
                "agent_id": agent_id,
                "message_id": message_id,
                "error": str(exc),
            })
            return json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
