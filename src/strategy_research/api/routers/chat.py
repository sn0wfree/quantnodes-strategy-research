"""Chat API — send message + SSE event stream with real LLM integration。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..session.bridge_v2 import attach_eventstore_to_sse
from ..session.service import SessionService
from ..session.store import SessionStore
from ..sse_buffer import sse_buffer
from strategy_research.core.agent.event_store import EventStore

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared EventStore + SessionService (singleton) ──────────────────────────
# Replaced EventBus + EventBusV2 with EventStore (Phase 7+8).
# EventStore handles all three sinks:
#   1. event_log (SQLite, source of truth)
#   2. SSE push (via sse_pusher callback → SSEEventBuffer)
#   3. messages + message_parts (via Projector.flush, flush_to_messages=True)

_event_store = EventStore()
attach_eventstore_to_sse(_event_store)

# True process-wide singleton. Recreating the service per request was a
# real bug: queues/tasks/pause state live on the instance, so cancel,
# queue_full and FIFO ordering silently broke (two parallel AgentLoops
# per session). Keyed by db path so tests/workspace switches re-create.
_session_service_cache: dict[str, SessionService] = {}


def _get_session_service() -> SessionService:
    """Return the process-wide singleton SessionService for the DB.

    Uses EventStore for triple-write:
    1. event_log (persistent source of truth)
    2. SSE push (via sse_pusher callback → SSEEventBuffer)
    3. messages + message_parts tables via Projector.flush (flush_to_messages=True)
    """
    from .web_session import _get_db_path

    db_path = _get_db_path()
    service = _session_service_cache.get(db_path)
    if service is None:
        store = SessionStore(db_path=db_path)
        es = EventStore(db_path=db_path, flush_to_messages=True)
        attach_eventstore_to_sse(es)
        service = SessionService(store=store, event_bus=es)
        _session_service_cache[db_path] = service
    return service


# ── LLM Config ────────────────────────────────────────────────────────


def _build_llm_config():
    """Build LLMConfig from env/settings. Same logic as TUI's __main__.py."""
    try:
        from pathlib import Path

        from dotenv import load_dotenv
        load_dotenv(Path.home() / ".quantnodes" / ".env", override=True)
    except Exception:
        pass
    try:
        from strategy_research.core.llm.config import LLMConfig
        cfg = LLMConfig.load()
        return cfg
    except Exception as exc:
        logger.warning("Failed to load LLMConfig: %s", exc)
        return None


class ChatMessage(BaseModel):
    session_id: str
    content: str
    images: Optional[list[str]] = None
    agent_id: Optional[str] = None


class SendMessageResponse(BaseModel):
    message_id: str
    user_message_id: str
    assistant_message_id: str
    event_id: str
    status: str = "queued"
    attempt_id: Optional[str] = None


# ── PartAccumulator (replaces inline on_event closure) ──────────────────────


class _PartAccumulator:
    """Accumulates LLM streaming parts and pushes events to SSE buffer.

    Replaces the inline ``on_event`` closure in ``_run_agent_loop_background``
    to reduce C901 complexity from 31 to <10.
    """

    def __init__(
        self,
        session_id: str,
        message_id: str,
    ) -> None:
        self._session_id = session_id
        self._message_id = message_id
        self.parts: list[dict[str, Any]] = []

    def handle(self, event_type: str, data: dict) -> None:
        event_data = {**data, "message_id": self._message_id}
        sse_buffer.push(
            event_type,
            json.dumps(event_data, ensure_ascii=False),
            self._session_id,
        )
        self._route(event_type, event_data)

    def _route(self, event_type: str, data: dict) -> None:
        handler = {
            "text.started": self._on_text_started,
            "text_delta": self._on_text_delta,
            "text.ended": self._on_text_ended,
            "tool_call": self._on_tool_call,
            "tool_result": self._on_tool_result,
            "tool_progress": self._on_tool_progress,
            "thinking_delta": self._on_thinking_delta,
            "thinking_done": self._on_thinking_done,
        }.get(event_type)
        if handler is not None:
            handler(data)

    def _on_text_started(self, data: dict) -> None:
        text_id = data.get("text_id")
        if text_id and not any(
            p.get("type") == "text" and p.get("id") == text_id
            for p in reversed(self.parts)
        ):
            self.parts.append({"type": "text", "id": text_id, "text": ""})

    def _on_text_delta(self, data: dict) -> None:
        text_id = data.get("text_id")
        text = data.get("text", "")
        if not text_id:
            return
        for p in reversed(self.parts):
            if p.get("type") == "text" and p.get("id") == text_id:
                p["text"] += text
                return
        self.parts.append({"type": "text", "id": text_id, "text": text})

    def _on_text_ended(self, data: dict) -> None:
        text_id = data.get("text_id")
        final_text = data.get("text", "")
        if text_id:
            for p in reversed(self.parts):
                if p.get("type") == "text" and p.get("id") == text_id:
                    p["text"] = final_text
                    return

    def _on_tool_call(self, data: dict) -> None:
        self.parts.append({
            "type": "tool_call",
            "id": data.get("id"),
            "name": data.get("name"),
            "arguments": data.get("arguments"),
        })

    def _on_tool_result(self, data: dict) -> None:
        for p in reversed(self.parts):
            if p.get("type") == "tool_call" and p.get("id") == data.get("id"):
                p["result"] = data.get("result")
                p["status"] = data.get("status", "done")
                return

    def _on_tool_progress(self, data: dict) -> None:
        for p in reversed(self.parts):
            if p.get("type") == "tool_call" and p.get("id") == data.get("id"):
                p["progress"] = data.get("steps", [])
                return

    def _on_thinking_delta(self, data: dict) -> None:
        delta = data.get("delta", "")
        if self.parts and self.parts[-1].get("type") == "thinking":
            self.parts[-1]["text"] += delta
        else:
            self.parts.append({"type": "thinking", "text": delta})

    def _on_thinking_done(self, data: dict) -> None:
        if self.parts and self.parts[-1].get("type") == "thinking":
            self.parts[-1]["status"] = "done"

    @property
    def assistant_content(self) -> str:
        return "".join(
            p.get("text", "") for p in self.parts if p.get("type") == "text"
        )


# ── Helpers for _run_agent_loop_background ──────────────────────────────


def _persist_user_message(session_id: str, content: str) -> None:
    """Persist user message to DB."""
    from .web_session import persist_message
    persist_message(session_id=session_id, role="user", content=content)


def _auto_title_and_notify(session_id: str, task: str) -> None:
    """Auto-title session and notify frontend via SSE."""
    from .web_session import _get_db, auto_title_session
    new_title = auto_title_session(session_id, task)
    if not new_title:
        return
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT message_count, starred, tags_json, archived "
            "FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        meta_update = {
            "session_id": session_id,
            "title": new_title,
            "message_count": row["message_count"] if row else 0,
            "starred": bool(row["starred"]) if row else False,
            "tags": json.loads(row["tags_json"]) if row and row["tags_json"] else [],
            "archived": bool(row["archived"]) if row else False,
        }
        sse_buffer.push(
            "session_meta_updated",
            json.dumps(meta_update, ensure_ascii=False),
            session_id,
        )
    except Exception as exc:
        logger.warning("Failed to emit session_meta_updated: %s", exc)


def _persist_assistant_message(
    session_id: str,
    content: str,
    parts: list[dict[str, Any]],
    message_id: str,
    cfg: Any = None,
) -> None:
    """Persist assistant message to DB."""
    from .web_session import persist_message
    persist_message(
        session_id=session_id, role="assistant", content=content,
        parts=parts or None,
        metadata={"model": getattr(cfg, "model", None)} if cfg else None,
        message_id=message_id,
    )


async def _emit_config_error(session_id: str, message_id: str) -> None:
    """Emit SSE error events when LLM is not configured."""
    err_data = {"message_id": message_id, "error": "LLM 未配置。请设置 OPENAI_API_KEY 环境变量。"}
    sse_buffer.push("error", json.dumps(err_data), session_id)
    sse_buffer.push(
        "agent_done",
        json.dumps({"message_id": message_id, "status": "error"}),
        session_id,
    )


def _emit_agent_done(session_id: str, message_id: str, answer: str | None) -> None:
    """Emit agent_done SSE event."""
    sse_buffer.push(
        "agent_done",
        json.dumps({
            "message_id": message_id,
            "status": "success" if answer else "empty",
            "answer_length": len(answer) if answer else 0,
        }),
        session_id,
    )


async def _emit_agent_error(session_id: str, message_id: str, exc: Exception) -> None:
    """Emit error + agent_done SSE events on AgentLoop failure."""
    sse_buffer.push(
        "error",
        json.dumps({"message_id": message_id, "error": str(exc)}),
        session_id,
    )
    sse_buffer.push(
        "agent_done",
        json.dumps({"message_id": message_id, "status": "error"}),
        session_id,
    )


async def _run_agent_loop_background(
    session_id: str,
    message_id: str,
    task: str,
):
    """Run AgentLoop in background, pushing events to SSE buffer."""
    import os

    _persist_user_message(session_id, task)
    _auto_title_and_notify(session_id, task)

    accumulator = _PartAccumulator(session_id, message_id)

    if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
        await _run_test_script(session_id, message_id, task, accumulator.parts)
        _persist_assistant_message(session_id, accumulator.assistant_content, accumulator.parts, message_id)
        return

    cfg = _build_llm_config()
    if cfg is None or not cfg.api_key:
        await _emit_config_error(session_id, message_id)
        _persist_assistant_message(session_id, "LLM 未配置", [{"type": "error", "message": "LLM 未配置"}], message_id)
        return

    try:
        from strategy_research.core.agent.chat_loop import build_chat_agent_loop
        from strategy_research.core.agent.memory_manager import (
            get_default_memory_manager,
        )

        mm = get_default_memory_manager()
        history = await mm.get(session_id) or []

        loop = build_chat_agent_loop(
            config=cfg,
            session_id=session_id,
            role="chat",
            on_event=accumulator.handle,
            workspace=None,
        )

        result = await loop.arun(task)

        if result.answer:
            await mm.append(session_id, "user", task)
            await mm.append(session_id, "assistant", result.answer)

        _emit_agent_done(session_id, message_id, result.answer)
        _persist_assistant_message(session_id, result.answer or accumulator.assistant_content, accumulator.parts, message_id, cfg)

    except Exception as exc:
        logger.error("AgentLoop failed for session %s: %s", session_id, exc, exc_info=True)
        await _emit_agent_error(session_id, message_id, exc)
        _persist_assistant_message(session_id, f"[error] {exc}", [{"type": "error", "message": str(exc)}], message_id)


async def _run_test_script(
    session_id: str,
    message_id: str,
    task: str,
    accumulated_parts: Optional[list[dict[str, Any]]] = None,
):
    """Emit a scripted sequence of SSE events for E2E testing.

    Mirrors what the real AgentLoop would emit:
    - thinking_start → thinking_delta (×N) → thinking_done
    - text_delta (×N, simulating streaming response)
    - agent_done

    Events are spaced 30ms apart to keep tests fast but realistic.

    If ``accumulated_parts`` is provided, fills it with the same text/thinking
    parts so persistence can save the assistant message.
    """
    reply_parts = [
        f"这是对「{task}」的脚本化回复。",
        " 主要演示 SSE 流式事件分发链路。",
        " 客户端 useSSE hook 会把每个 text_delta 累积到 streamingText。",
        "\n\n**测试要点**：",
        "\n- 事件 message_id 关联",
        "\n- streamingText 累积",
        "\n- agent_done 清空 streamingMessageId",
    ]
    if accumulated_parts is None:
        accumulated_parts = []
    try:
        # Phase 1: thinking block (collapsed by default)
        sse_buffer.push(
            "thinking_start",
            json.dumps({"message_id": message_id}, ensure_ascii=False),
            session_id,
        )
        thinking_chunks = ["分析用户问题", " → 检索相关策略", " → 准备回复"]
        thinking_text = "".join(thinking_chunks)
        accumulated_parts.append({"type": "thinking", "text": thinking_text, "status": "streaming"})
        for chunk in thinking_chunks:
            await asyncio.sleep(0.03)
            sse_buffer.push(
                "thinking_delta",
                json.dumps({"message_id": message_id, "delta": chunk}, ensure_ascii=False),
                session_id,
            )
        # Mark thinking done
        if accumulated_parts and accumulated_parts[-1].get("type") == "thinking":
            accumulated_parts[-1]["status"] = "done"
        sse_buffer.push(
            "thinking_done",
            json.dumps({"message_id": message_id}, ensure_ascii=False),
            session_id,
        )

        # Phase 2: streaming text reply — accumulate as single text part
        full_text = "".join(reply_parts)
        accumulated_parts.append({"type": "text", "text": full_text})
        for part in reply_parts:
            await asyncio.sleep(0.03)
            sse_buffer.push(
                "text_delta",
                json.dumps({"message_id": message_id, "text": part}, ensure_ascii=False),
                session_id,
            )

        # Phase 3: completion signal
        sse_buffer.push(
            "agent_done",
            json.dumps({
                "message_id": message_id,
                "status": "success",
                "answer_length": sum(len(p) for p in reply_parts),
            }, ensure_ascii=False),
            session_id,
        )
    except asyncio.CancelledError:
        # Test cancellation handling matches production
        sse_buffer.push(
            "agent_done",
            json.dumps({"message_id": message_id, "status": "cancelled"}, ensure_ascii=False),
            session_id,
        )
        raise


@router.post("/send_async", response_model=SendMessageResponse)
async def send_async(body: ChatMessage, request: Request):
    """Send a message asynchronously. Enqueues into per-session FIFO queue.

    Returns message_id + event_id for confirmation.
    Frontend should listen to /events SSE stream for real-time response.

    Uses the unified SessionService (also shared with TUI). New messages
    are appended to a per-session queue and processed FIFO; if the queue
    is full (hard limit 10) the API returns 429.
    """
    logger.info("[SEND] session=%s content_len=%d content_preview=%s",
                body.session_id, len(body.content), body.content[:50])

    # Ownership check: only the session owner may post messages
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

    # ── /goal command intercept ────────────────────────────────────────
    if body.content.strip().startswith("/goal"):
        return await _handle_goal_command(body)

    # ── /compact command intercept ────────────────────────────────────
    if body.content.strip() == "/compact":
        return await _handle_compact_command(body)

    # Delegate to SessionService — it handles DB persistence, queueing,
    # AgentLoop execution, history context, and event emission.
    service = _get_session_service()
    # Chat mode: bounded iteration budget from llm.json (default 50).
    # Prevents a failing tool loop from growing the prompt unboundedly.
    from strategy_research.core.llm.config import LLMConfig
    try:
        _cfg = LLMConfig.load()
        _max_iter = _cfg.max_iterations
    except Exception:
        _max_iter = 50
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
        max_iterations=_max_iter,
    )

    # Queue-full guard: SessionService returns {"error": "queue_full", ...}
    # when the session already has _QUEUE_LIMIT items waiting.
    if result.get("error") == "queue_full":
        logger.warning("[SEND] queue_full session=%s limit=%d current=%d",
                       body.session_id, result["limit"], result["current_size"])
        raise HTTPException(
            status_code=429,
            detail={
                "error": "queue_full",
                "limit": result["limit"],
                "current_size": result["current_size"],
            },
        )

    logger.info("[SEND] ok session=%s user_msg=%s attempt=%s status=%s",
                body.session_id, result.get("user_message_id"),
                result.get("attempt_id"), result.get("status"))

    return SendMessageResponse(
        message_id=result["user_message_id"],
        user_message_id=result["user_message_id"],
        assistant_message_id=result["assistant_message_id"],
        event_id="",
        status=result.get("status", "processing"),
        attempt_id=result.get("attempt_id"),
    )


# ── /goal command handler ────────────────────────────────────────────


async def _handle_goal_command(body: ChatMessage) -> SendMessageResponse:
    """Handle /goal slash commands without going through AgentLoop.

    B5: All persistence via EventStore → projector.flush(). No direct
    persist_message / sse_buffer.push calls.
    """
    from ...core.goal import EvidenceInput, GoalStatus, GoalStore
    from ...core.goal.context import default_goal_criteria

    session_id = body.session_id
    content = body.content.strip()

    parts = content.split(None, 2)
    subcmd = parts[1].lower() if len(parts) > 1 else "status"
    args = parts[2] if len(parts) > 2 else ""

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())

    service = _get_session_service()
    event_bus = service.event_bus

    event_bus.emit(session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": content,
        "role": "user",
    })

    try:
        with GoalStore() as store:
            response_text = _dispatch_goal_command(subcmd, args, session_id, store)
    except Exception as exc:
        logger.exception("goal command failed: %s", subcmd)
        response_text = f"Goal command failed: {exc}"

    _emit_goal_response(event_bus, session_id, assistant_msg_id, response_text)

    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


def _dispatch_goal_command(
    subcmd: str, args: str, session_id: str, store: Any,
) -> str:
    """Dispatch goal subcommand to handler."""
    handlers = {
        "start": _goal_start,
        "create": _goal_start,
        "status": _goal_status,
        "": _goal_status,
        "evidence": _goal_evidence,
        "ev": _goal_evidence,
        "complete": _goal_complete,
        "done": _goal_complete,
        "cancel": _goal_cancel,
        "help": _goal_help,
    }
    handler = handlers.get(subcmd)
    if handler is None:
        return f"Unknown subcommand: {subcmd}. Use /goal help for usage."
    if handler is _goal_help:
        return handler()
    return handler(args, session_id, store)


def _goal_start(args: str, session_id: str, store: Any) -> str:
    from ...core.goal.context import default_goal_criteria
    objective = args or "Research goal"
    goal = store.replace_goal(
        session_id=session_id, objective=objective,
        criteria=default_goal_criteria(),
    )
    return (
        f"Goal created: {goal.goal_id[:12]}...\n"
        f"Objective: {goal.objective}\n"
        f"Status: {goal.status.value}"
    )


def _goal_status(args: str, session_id: str, store: Any) -> str:
    current = store.get_current_goal(session_id)
    if current is None:
        return "No active goal. Use /goal start <objective> to create one."
    snapshot = store.get_current_snapshot(session_id)
    criteria = snapshot.get("criteria", []) if snapshot else []
    evidence_count = snapshot.get("evidence_count", 0) if snapshot else 0
    return (
        f"Goal: {current.goal_id[:12]}...\n"
        f"Objective: {current.objective}\n"
        f"Status: {current.status.value}\n"
        f"Progress: {current.progress_percent:.0f}%\n"
        f"Criteria: {len(criteria)} | Evidence: {evidence_count}"
    )


def _goal_evidence(args: str, session_id: str, store: Any) -> str:
    from ...core.goal import EvidenceInput
    current = store.get_current_goal(session_id)
    if current is None:
        return "No active goal. Create one first with /goal start <objective>."
    text = args or "No evidence text provided"
    evidence = EvidenceInput(text=text, source_type="chat")
    record = store.append_evidence(
        session_id=session_id, goal_id=current.goal_id,
        expected_goal_id=current.goal_id, evidence=evidence,
    )
    updated = store.get_current_goal(session_id)
    return (
        f"Evidence added: {record.evidence_id[:12]}...\n"
        f"Progress: {updated.progress_percent:.0f}%"
    )


def _goal_complete(args: str, session_id: str, store: Any) -> str:
    current = store.get_current_goal(session_id)
    if current is None:
        return "No active goal to complete."
    recap = args or None
    updated = store.complete_lite(
        session_id=session_id, goal_id=current.goal_id,
        expected_goal_id=current.goal_id, recap=recap,
    )
    return (
        f"Goal completed: {updated.goal_id[:12]}...\n"
        f"Status: {updated.status.value}"
    )


def _goal_cancel(args: str, session_id: str, store: Any) -> str:
    from ...core.goal import GoalStatus
    current = store.get_current_goal(session_id)
    if current is None:
        return "No active goal to cancel."
    recap = args or None
    updated = store.update_status(
        session_id=session_id, goal_id=current.goal_id,
        expected_goal_id=current.goal_id,
        status=GoalStatus.CANCELLED, recap=recap,
    )
    return (
        f"Goal cancelled: {updated.goal_id[:12]}...\n"
        f"Status: {updated.status.value}"
    )


def _goal_help() -> str:
    return (
        "/goal start <objective>  — create a new goal\n"
        "/goal status             — show current goal\n"
        "/goal evidence <text>    — add evidence\n"
        "/goal complete [recap]   — mark complete\n"
        "/goal cancel [recap]     — cancel goal\n"
        "/goal help               — this message"
    )


def _emit_goal_response(
    event_bus: Any, session_id: str, assistant_msg_id: str, response_text: str,
) -> None:
    """Emit goal response as 3-step text protocol."""
    goal_text_id = str(uuid.uuid4())
    event_bus.emit(session_id, "text.started", {
        "message_id": assistant_msg_id, "text_id": goal_text_id,
    })
    event_bus.emit(session_id, "text_delta", {
        "message_id": assistant_msg_id, "text_id": goal_text_id, "text": response_text,
    })
    event_bus.emit(session_id, "text.ended", {
        "message_id": assistant_msg_id, "text_id": goal_text_id, "text": response_text,
    })
    event_bus.emit(session_id, "assistant_message", {
        "message_id": assistant_msg_id, "content": response_text,
        "message_type": "assistant", "metadata": {"model": "goal-handler"},
    })
    event_bus.emit(session_id, "agent_done", {
        "message_id": assistant_msg_id, "status": "success",
    })


# ── /compact command handler ──────────────────────────────────────


async def _handle_compact_command(body: ChatMessage) -> SendMessageResponse:
    """Handle /compact command — compress session history in-place."""
    import uuid

    service = _get_session_service()
    cfg = _build_llm_config()

    # B5: user message persisted via EventStore → projector.flush()
    user_msg_id = str(uuid.uuid4())

    # Execute compaction
    try:
        result = await service.compact_history(
            session_id=body.session_id,
            config=cfg.compact_config if cfg else None,
        )
        layers = result.get("layers", [])
        before = result.get("before_tokens", 0)
        after = result.get("after_tokens", 0)
        summary = result.get("summary", "")

        if layers:
            response_text = f"✅ 上下文已压缩: {', '.join(layers)}（{before} → {after} tokens）"
            if summary:
                response_text += f"\n\n{summary}"
        else:
            response_text = "ℹ️ 上下文无需压缩，当前 token 使用量在阈值以下。"
    except Exception as exc:
        logger.exception("compact_history failed")
        response_text = f"❌ 压缩失败: {exc}"

    # B5: assistant message persisted via EventStore → projector.flush()
    assistant_msg_id = str(uuid.uuid4())

    # Emit SSE events (3-step text protocol) — also flushes to messages table
    event_bus = service.event_bus
    compact_text_id = str(uuid.uuid4())
    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": compact_text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": response_text,
        "text_id": compact_text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": compact_text_id,
        "text": response_text,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": response_text,
        "message_type": "assistant",
    })
    event_bus.emit(body.session_id, "agent_done", {
        "message_id": assistant_msg_id,
        "status": "completed",
    })

    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


class CancelRequest(BaseModel):
    session_id: str
    attempt_id: Optional[str] = None


@router.post("/cancel")
async def cancel_attempt(body: CancelRequest, request: Request):
    """Cancel an in-flight agent attempt for a session.

    If attempt_id is provided, cancels that specific attempt.
    Otherwise, cancels any active attempt for the session.

    After cancellation the per-session queue is **paused** (queue_paused
    SSE event). Use ``POST /chat/queue/resume`` to continue processing.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
    service = _get_session_service()

    # Prefer the session-scoped cancel (cancels the per-attempt task; the
    # consumer loop catches CancelledError and pauses the queue).
    if body.attempt_id:
        ok = service.cancel(body.attempt_id)
        if ok:
            return {"status": "cancelled", "attempt_id": body.attempt_id}

    # Fallback: cancel by session_id only
    ok = service.cancel_session(body.session_id)
    if ok:
        return {"status": "cancelled", "session_id": body.session_id}

    return {"status": "no_active_attempt", "session_id": body.session_id}


class QueueResumeRequest(BaseModel):
    session_id: str


@router.post("/queue/resume")
async def queue_resume(body: QueueResumeRequest, request: Request):
    """Resume a paused per-session queue after an explicit cancel.

    Returns ``{"ok": true, "session_id": ...}`` if the queue was paused
    and is now resumed; ``{"ok": false}`` if no paused queue exists.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)
    service = _get_session_service()
    ok = service.resume_queue(body.session_id)
    return {"ok": ok, "session_id": body.session_id}


@router.post("/send")
async def send_sync(body: ChatMessage, request: Request):
    """Send a message synchronously (non-streaming).

    Waits for the full response. Use /send_async for streaming.

    Aligned with send_async: ownership check + unified SessionService
    (event-log persistence, FIFO queue, conversation-history context).
    The reply is collected by waiting on the attempt instead of
    streaming events over SSE.
    """
    # Ownership check: only the session owner may post messages
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), body.session_id, user_id)

    service = _get_session_service()
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
    )
    if result.get("error") == "queue_full":
        raise HTTPException(status_code=429, detail=result)

    attempt_id = result.get("attempt_id")
    outcome = await service.wait_for_attempt(
        body.session_id, attempt_id,
    ) if attempt_id else None
    if outcome is None:
        raise HTTPException(
            status_code=504, detail="LLM response timed out",
        )
    return {
        "message_id": result.get("message_id"),
        "attempt_id": attempt_id,
        "reply": outcome.get("summary", ""),
        "status": outcome.get("status"),
    }


@router.get("/events")
async def chat_events(
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    last_event_id_query: Optional[str] = Query(None, alias="Last-Event-ID"),
    request: Request = None,
):
    """SSE event stream for a session."""
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    resolved_last_event_id = last_event_id or last_event_id_query or ""
    logger.info("[SSE] client connected session=%s last_event_id=%s", session_id, resolved_last_event_id)

    notification_event = sse_buffer.register_session(session_id)

    return StreamingResponse(
        _event_generator(session_id, resolved_last_event_id, notification_event),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_generator(
    session_id: str,
    last_id: str,
    notification_event: Any,
):
    """SSE event generator: replay missed events then stream live."""
    logger.info("[SSE] generator started session=%s", session_id)
    yield ": connected\n\n"
    yield "retry: 3000\n\n"

    event_count = 0
    try:
        last_id, event_count, replay_lines = await _replay_missed(last_id, session_id, event_count)
        for line in replay_lines:
            yield line
        while True:
            try:
                await asyncio.wait_for(notification_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                event_count += 1
                yield _heartbeat_sse(event_count)
                if event_count % 4 == 0:
                    logger.debug("[SSE] heartbeat #%d session=%s", event_count, session_id)
                continue

            notification_event.clear()
            new_events = sse_buffer.get_events_since(session_id, last_id)
            for evt in new_events:
                yield _format_sse(evt)
                last_id = evt.id
                event_count += 1
                if evt.event:
                    logger.debug("[SSE] event=%s session=%s id=%s", evt.event, session_id, evt.id)
    except asyncio.CancelledError:
        logger.info("[SSE] client disconnected session=%s reason=cancelled events=%d", session_id, event_count)
    except Exception as exc:
        logger.error("[SSE] generator error session=%s: %s", session_id, exc)
        raise
    finally:
        sse_buffer.unregister_session(session_id, notification_event)
        logger.info("[SSE] generator ended session=%s total_events=%d", session_id, event_count)


async def _replay_missed(
    last_id: str, session_id: str, event_count: int,
) -> tuple[str, int, list[str]]:
    """Replay missed events from last_event_id, return (last_id, event_count, lines)."""
    lines: list[str] = []
    if last_id:
        missed = sse_buffer.replay_from(last_id, session_id)
        for evt in missed:
            lines.append(_format_sse(evt))
            last_id = evt.id
        logger.debug("[SSE] replayed %d missed events session=%s", len(missed), session_id)

    existing = sse_buffer.get_events_since(session_id, last_id)
    for evt in existing:
        lines.append(_format_sse(evt))
        last_id = evt.id
    if existing:
        logger.debug("[SSE] flushed %d existing events session=%s", len(existing), session_id)
    return last_id, event_count, lines


def _format_sse(evt) -> str:
    """Format an SSEEvent as an SSE text line."""
    lines = []
    if evt.id:
        lines.append(f"id: {evt.id}")
    if evt.event:
        lines.append(f"event: {evt.event}")
    for line in evt.data.split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _heartbeat_sse(count: int) -> str:
    """SSE comment-line keep-alive (opencode style).

    Pure comment lines are ignored by the browser's EventSource
    (no onerror, no onmessage) but they keep the TCP connection alive
    and re-arm the browser's 3-minute idle timeout. See
    opencode packages/server/src/handlers/event.ts:37 for the
    reference implementation.
    """
    return ": heartbeat\n\n"
