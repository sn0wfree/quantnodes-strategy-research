"""入口 adapter（阶段 3a）—— 把各入口的请求翻译成 WorkRequest。

职责边界（契约 §1）：
- 渠道 adapter 只做：协议字段剥除 → WorkRequest 构造 → 提交 → 拿回执。
- **不做**任何裁决（准入/排队/预算都在 Gateway 内）。
- **不认识** SessionController（消费由 Gateway 侧驱动）。

feature flag（`QNODES_NEW_ENTRY`，缺省 off）：
- off：现有路径照旧（可回退）
- on：走新架构（WorkRequest → EntryPoint → mailbox）
灰度期结束（阶段 3 收尾）后移除 flag，删除旧路径。
"""
from __future__ import annotations

import os

from strategy_research.core.orchestration import (
    EntryPoint,
    Receipt,
    SourceKind,
    WorkKind,
    WorkRequest,
)


def new_entry_enabled() -> bool:
    """feature flag：chat/CLI 入口是否走新架构（阶段 3b 起默认开启）。

    设 QNODES_NEW_ENTRY=0 可回退旧路径（灰度期保留）。
    """
    value = os.environ.get("QNODES_NEW_ENTRY", "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


def study_entry_enabled() -> bool:
    """scheduler/study 入口是否走统一入口（**阶段 3c 起默认开启**）。

    B1-a/B1-b 交付后 study 已有阶段图执行能力，3c 完成提交级接线，
    故翻默认为开。回退方式：QNODES_NEW_ENTRY_STUDY=0（旧 asyncio.Queue
    路径保留到阶段 4 删除）。
    """
    value = os.environ.get("QNODES_NEW_ENTRY_STUDY", "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


def research_graph_enabled() -> bool:
    """phases 引擎分支是否走 GraphEngine 阶段图（**阶段 3c 起默认开启**）。

    这是「引擎路径」门，与两个入口门正交。B1-b 已用等价性测试钉死
    （RoundOutcome 逐字段相等）；回退方式：QNODES_RESEARCH_GRAPH=0。
    """
    value = os.environ.get("QNODES_RESEARCH_GRAPH", "").strip().lower()
    if value in ("0", "false", "no", "off"):
        return False
    return True


def api_chat_request(session_id: str, content: str) -> WorkRequest:
    """把 API chat 请求翻译成 WorkRequest。

    session_key 用 `chat:<session_id>`——同一物理会话的请求进同一 mailbox
    （session=串行域）。协议字段（HTTP headers 等）在此剥除，不进入 payload。
    """
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.API_CHAT,
        session_key=f"chat:{session_id}",
        payload={"text": content},
    )


def cli_request(session_key: str, text: str) -> WorkRequest:
    """CLI 请求：来源 CLI → 默认 followup（五行表）。"""
    key = session_key if ":" in session_key else f"chat:{session_key}"
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.CLI,
        session_key=key,
        payload={"text": text},
    )


def mcp_request(session_key: str, payload: dict) -> WorkRequest:
    """MCP 请求：来源 MCP → 默认 followup（保守排队）。"""
    key = session_key if ":" in session_key else f"mcp:{session_key}"
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.MCP,
        session_key=key,
        payload=dict(payload),
    )


def scheduler_request(session_key: str, payload: dict) -> WorkRequest:
    """定时任务请求：来源 scheduler → 默认 interrupt。"""
    key = session_key if ":" in session_key else f"automation:{session_key}"
    return WorkRequest(
        kind=WorkKind.AUTOMATION,
        source=SourceKind.SCHEDULER,
        session_key=key,
        payload=dict(payload),
    )


def submit_via_entry(entry: EntryPoint, request: WorkRequest) -> Receipt:
    """统一提交点：所有 adapter 都经由这里（便于审计/打点）。"""
    return entry.submit(request)
