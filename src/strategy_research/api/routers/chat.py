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

from ..session.service import SessionService
from ..session.store import SessionStore
from ..sse_buffer import sse_buffer
from ._task_utils import log_task_exception
from strategy_research.core.agent.event_store import EventStore

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared EventStore + SessionService (singleton) ──────────────────────────
# The EventStore is created per-DB-path inside _get_session_service()
# (one per workspace). There is NO module-level EventStore instance —
# the previous ``_event_store = EventStore()`` was a dead instance that
# opened an empty ``~/.quantnodes/sessions.db`` and was never emitted
# to, but it held a file descriptor and confused DB-path unification.
# See core.agent.memory_manager.resolve_session_db_path for the unified
# DB path resolution shared by EventStore and web_session.

# True process-wide singleton. Recreating the service per request was a
# real bug: queues/tasks/pause state live on the instance, so cancel,
# queue_full and FIFO ordering silently broke (two parallel AgentLoops
# per session). Keyed by db path so tests/workspace switches re-create.
_session_service_cache: dict[str, SessionService] = {}

# ── Tier 1 A1: permission gateway ──────────────────────────────────
# One process-wide PermissionGateway (mirrors SessionService). Wired
# with an ``on_request`` hook that pushes a ``permission_request``
# SSE event for every ASK verdict; the front-end answers via
# ``POST /api/chat/permission/respond`` which calls
# ``gateway.respond(tool_call_id, ...)``.
_permission_gateway_cache: dict[str, "PermissionGateway"] = {}


def _get_permission_gateway(request: Request | None = None) -> "PermissionGateway | None":
    """Lazy-init the process-wide gateway.

    The ``on_request`` hook is wired on first use so we can attach the
    SSE buffer without circular imports.
    """
    from ...core.permission import PermissionGateway

    gateway = _permission_gateway_cache.get("default")
    if gateway is not None:
        return gateway

    def _push_sse(tool_call_id, decision, args):
        # Lazy import — sse_buffer is a heavy module.
        try:
            from ...api.sse_buffer import sse_buffer  # local import
            from ...api.session.event_v2 import EventType

            session_id = args.get("__session_id__") or ""
            if not session_id:
                # No session scope = no subscriber can see this.
                # Drop on the floor (and log so operators notice).
                logger.warning(
                    "permission request without session_id; dropping",
                )
                return
            # Tool name is implicit from the EventType subscriber;
            # payload carries call_id + args for the dialog UI.
            import json as _json
            payload = {
                "tool_call_id": tool_call_id,
                "tool_name": args.get("__tool_name__", ""),
                "args": _safe_payload(args),
                "pattern": decision.pattern,
                "target": decision.target,
            }
            sse_buffer.push(
                EventType.PERMISSION_REQUEST,
                _json.dumps(payload, ensure_ascii=False),
                session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("permission SSE push failed: %s", exc)

    gateway = PermissionGateway(
        on_request=_push_sse,
    )
    _permission_gateway_cache["default"] = gateway
    return gateway


def _safe_payload(args: dict) -> dict:
    """Drop large fields before SSE-serialising the request."""
    out = {}
    for k, v in args.items():
        if k.startswith("__") and k.endswith("__"):
            continue  # internal marker
        s = repr(v)
        out[k] = s[:200] + "…" if len(s) > 200 else v
    return out


def _get_session_service() -> SessionService:
    """Return the process-wide singleton SessionService for the DB.

    Uses EventStore for triple-write:
    1. event_log (persistent source of truth)
    2. SSE push (via sse_pusher callback → SSEEventBuffer)
    3. messages + message_parts tables via Projector.flush (flush_to_messages=True)
    """
    from .web_session import _get_db_path
    from ..session.bridge_v2 import attach_eventstore_to_sse

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
    mode: Optional[str] = None          # "plan" | "build" (None = session default)
    model: Optional[str] = None         # 会话级模型覆盖
    thinking: Optional[str] = None      # "off" | "on" | "auto"


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
    claim_validation: dict[str, Any] | None = None,
) -> None:
    """Persist assistant message to DB."""
    from .web_session import persist_message
    metadata: dict[str, Any] = {"model": getattr(cfg, "model", None)} if cfg else {}
    if claim_validation:
        metadata["claim_validation"] = claim_validation
    persist_message(
        session_id=session_id, role="assistant", content=content,
        parts=parts or None,
        metadata=metadata or None,
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
            enable_goal_injection=True,  # long-horizon: continue until goal criteria met
            # Tier 1 A1: wire the permission gate so ASK verdicts
            # trigger an SSE permission_request that the front-end
            # answers via POST /api/chat/permission/respond.
            permission_evaluator=_get_permission_gateway(None).evaluator,
            permission_gateway=_get_permission_gateway(None),
        )

        result = await loop.arun(task)

        if result.answer:
            await mm.append(session_id, "user", task)
            await mm.append(session_id, "assistant", result.answer)

        _emit_agent_done(session_id, message_id, result.answer)
        _persist_assistant_message(
            session_id,
            result.answer or accumulator.assistant_content,
            accumulator.parts,
            message_id,
            cfg,
            claim_validation=result.metrics.get("claim_validation"),
        )

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

    # ── /study command intercept (study task system) ───────────────────
    if body.content.strip().startswith("/study"):
        return await _handle_study_command(body)

    # ── /compact command intercept ──────────────────────────────────────
    if body.content.strip() == "/compact":
        return await _handle_compact_command(body)

    # ── /clear command intercept (webui only — clears LLM-visible
    # history while preserving the persisted message log). Returns
    # immediately without invoking the LLM. The TUI does not use this
    # path; equivalent TUI behaviour is `clear_session`. ─────────────
    if body.content.strip() == "/clear":
        return await _handle_clear_command(body)

    # ── /help command intercept — returns a static cheat-sheet text
    # in a synthetic assistant message so the user sees it in context.
    # Skips LLM invocation. ──────────────────────────────────────────
    if body.content.strip() == "/help":
        return await _handle_help_command(body)

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
    # Shell tools are opt-in: off by default. Set SR_ALLOW_SHELL_TOOLS=1
    # in the server environment to enable run_command for the agent.
    import os
    _allow_shell = os.environ.get("SR_ALLOW_SHELL_TOOLS", "").lower() in ("1", "true", "yes")
    # Plan mode: single iteration (analysis only), no shell tools
    _mode = body.mode or "build"
    _max_iter_eff = 1 if _mode == "plan" else _max_iter
    _allow_shell_eff = False if _mode == "plan" else _allow_shell
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
        max_iterations=_max_iter_eff,
        allow_shell_tools=_allow_shell_eff,
        persona=body.agent_id,
        mode=_mode,
        model=body.model,
        thinking=body.thinking,
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

    # Emit goal SSE events so the frontend GoalTab updates in real-time
    _emit_goal_sse_event(event_bus, session_id, subcmd)

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
    """Create a goal + study (manual executor_type, no auto-submit).

    /goal start = /study start without automatic execution.
    The study record is created for tracking but not submitted to the scheduler.
    """
    from ...core.goal.context import default_goal_criteria
    from ...core.study import StudyStore

    objective = args or "Research goal"

    # Check for active study first
    with StudyStore() as study_store:
        active = study_store.get_active_study(session_id)
        if active is not None:
            return (
                f"Session already has an active study: {active.study_id[:12]}...\n"
                f"Status: {active.execution_status.value}\n"
                f"Cancel it first with /study cancel or wait for it to complete."
            )

    goal = store.replace_goal(
        session_id=session_id, objective=objective,
        criteria=default_goal_criteria(),
    )
    # Create a study record (manual executor, not submitted to scheduler)
    with StudyStore() as study_store:
        study = study_store.create_study(
            session_id=session_id, goal_id=goal.goal_id,
            objective=objective, workspace_path=_default_workspace(),
            strategy_name="manual", executor_type="manual",
            metric_targets=[],
        )
    return (
        f"Goal created: {goal.goal_id[:12]}...\n"
        f"Study created: {study.study_id[:12]}...\n"
        f"Objective: {goal.objective}\n"
        f"Status: {goal.status.value} (manual mode, no auto-execution)\n"
        f"Use /goal evidence <text> to add evidence manually."
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


def _emit_goal_sse_event(event_bus: Any, session_id: str, subcmd: str) -> None:
    """Emit goal SSE event after /goal command execution.

    Reads the current goal snapshot from GoalStore and emits a single
    full-snapshot ``goal_updated`` event (same payload builder as the
    chat-tool path — core/goal/events.py) so the frontend panel and
    the message-stream projector stay in sync.
    """
    from ...core.goal import GoalStore
    from ...core.goal.events import (
        CHANGE_TYPE_COMPLETE,
        CHANGE_TYPE_CREATE,
        CHANGE_TYPE_EVIDENCE,
        build_goal_updated_payload,
    )

    # Only emit for mutation commands
    if subcmd not in ("start", "create", "evidence", "ev", "complete", "done"):
        return

    if subcmd in ("start", "create"):
        change_type = CHANGE_TYPE_CREATE
    elif subcmd in ("evidence", "ev"):
        change_type = CHANGE_TYPE_EVIDENCE
    else:
        change_type = CHANGE_TYPE_COMPLETE

    payload = None
    try:
        with GoalStore() as store:
            payload = build_goal_updated_payload(
                session_id, store, change_type,
            )
    except Exception:
        logger.debug("failed to read goal for SSE emit", exc_info=True)
        return

    if payload is not None:
        event_bus.emit(session_id, "goal_updated", payload)


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


# ── /study command handler ──────────────────────────────────────────


async def _handle_study_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/study`` slash commands (light wrapper around the API).

    Supported:
        ``/study start "objective" [--workspace W] [--strategy S]
                       [--metric calmar>=0.5,sharpe>=0.3]
                       [--budget-turn N] [--budget-time S]
                       [--max-rounds N] [--behavior static|varying|improving]``
        ``/study status``
        ``/study list``
        ``/study pause <study_id>``
        ``/study resume <study_id>``
        ``/study cancel <study_id>``
        ``/study help``

    Uses the same TXT-style response protocol as /goal handlers (text
    started / delta / ended). State changes happen via the study router
    helpers so the scheduler emits study_* events upstream too.
    """
    import shlex
    import uuid

    session_id = body.session_id
    content = body.content.strip()

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus

    # Persist the user message before running the command (same triple as
    # chat: EventStore → projector.flush → messages table).
    event_bus.emit(session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": content,
        "role": "user",
    })

    try:
        response_text = _dispatch_study_command(content, session_id)
    except Exception as exc:
        logger.exception("study command failed")
        response_text = f"Study command failed: {exc}"
    print(f"[STUDY:chat] command response: {response_text[:100]}", flush=True)

    # Flush any pending workflow submits (created by ``/study start``)
    # on this loop before the response round-trips to the user.
    if _study_pending_submits:
        session_service = _get_session_service()
        for study, config, goal_id, objective, ws in _study_pending_submits:
            if config is None:
                # AEGIS: autoresearch → scheduler
                sched = _get_study_scheduler()
                import asyncio as _asyncio
                task = _asyncio.create_task(sched.submit(study))
                task.add_done_callback(log_task_exception)
            else:
                await _start_workflow_runner(
                    config, session_id, goal_id, objective, ws, session_service,
                )
        _study_pending_submits.clear()

    _emit_goal_response(event_bus, session_id, assistant_msg_id, response_text)

    return SendMessageResponse(
        message_id=user_msg_id, user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="", status="done",
    )


def _dispatch_study_command(content: str, session_id: str) -> str:
    """Parse and run a /study subcommand. ``content`` is the raw user text."""

    import shlex

    # Strip leading "/study"
    body = content[len("/study"):].strip()
    if not body:
        return _study_help()

    # Split into subcommand + rest (shlex to keep quoted objective intact).
    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        return f"Parse error: {exc}"
    subcmd = tokens[0].lower()
    rest = tokens[1:]

    if subcmd in ("help", "?"):
        return _study_help()
    if subcmd == "start":
        return _study_start_cmd(rest, session_id)
    if subcmd == "status":
        return _study_status_cmd(session_id)
    if subcmd == "list":
        return _study_list_cmd(rest)
    if subcmd in ("pause", "resume", "cancel"):
        if not rest:
            return f"/study {subcmd} requires a study_id"
        return _study_control_cmd(subcmd, rest[0])

    if subcmd in ("redirect", "directive"):
        # /study redirect <study_id> "<directive content>"
        if not rest:
            return "/study redirect requires a study_id and quoted content"
        target_study = rest[0]
        # Re-join remaining tokens so multi-word directives work without
        # having to escape every space.
        directive_text = " ".join(rest[1:]).strip().strip('"\'')
        if not directive_text:
            return "/study redirect requires quoted content after study_id"
        return _study_redirect_cmd(target_study, directive_text, session_id)

    # Else: unknown — show help. (Allows the user to say "/study foo bar".)
    return f"Unknown subcommand: {subcmd}\n" + _study_help()


def _study_help() -> str:
    return (
        "/study start \"<objective>\" [--workspace W] [--strategy S]\n"
        "            [--metric calmar>=0.5,sharpe>=0.3]\n"
        "            [--budget-turn N] [--budget-time S] [--max-rounds N]\n"
        "            [--monitor-interval S]   (Phase 3: post-completion drift check)\n"
        "            [--behavior static|varying|improving]\n"
        "  Create a study. The active session's goal ledger is created.\n"
        "/study status   — current study for this session\n"
        "/study list [status=queued|running|monitoring|complete|cancelled]\n"
        "/study pause <study_id>\n"
        "/study resume <study_id>\n"
        "/study cancel <study_id>\n"
        "/study redirect <study_id> \"<directive>\" — mid-exec redirect\n"
        "/study help     — this message"
    )


def _parse_study_flags(rest: list[str]) -> dict:
    """Tokenize free-form ``--flag value`` style flags."""
    flags = {
        "workspace_path": None, "strategy_name": None,
        "metric_targets": None, "executor_type": "autoresearch",
        "budget_turn": None, "budget_time_seconds": None, "max_rounds": None,
        "behavior": None, "monitor_interval_seconds": None,
    }
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--") and i + 1 < len(rest):
            key, val = tok[2:], rest[i + 1]
            if key in ("workspace", "workspace_path"):
                flags["workspace_path"] = val
            elif key in ("strategy", "strategy_name"):
                flags["strategy_name"] = val
            elif key == "metric":
                flags["metric_targets"] = _parse_metric_targets(val)
            elif key in ("budget-turn",):
                try:
                    flags["budget_turn"] = int(val)
                except ValueError:
                    pass
            elif key in ("budget-time",):
                try:
                    flags["budget_time_seconds"] = int(val)
                except ValueError:
                    pass
            elif key in ("max-rounds",):
                try:
                    flags["max_rounds"] = int(val)
                except ValueError:
                    pass
            elif key in ("monitor-interval",):
                try:
                    flags["monitor_interval_seconds"] = int(val)
                except ValueError:
                    pass
            elif key == "behavior":
                flags["behavior"] = val
            elif key in ("executor", "executor_type"):
                if val in ("autoresearch", "workflow"):
                    flags["executor_type"] = val
            i += 2
        else:
            i += 1
    return flags


def _parse_metric_targets(spec: str) -> list[dict] | None:
    """Parse a comma-separated ``calmar>=0.5`` spec → list of target dicts."""
    import re
    targets: list[dict] = []
    for chunk in spec.split(","):
        m = re.match(r"\s*([A-Za-z_]+)\s*(>=|<=|>|<|==)\s*(-?\d+(\.\d+)?)\s*$", chunk)
        if not m:
            continue
        targets.append({
            "name": m.group(1), "op": m.group(2), "value": float(m.group(3)),
        })
    return targets or None


def _default_workspace() -> str:
    """Return the process-default workspace path for /study defaults."""
    import os
    return os.environ.get("SR_WORKSPACE_PATH") or str(
        Path.home() / ".quantnodes-research"
    )


def _study_start_cmd(rest: list[str], session_id: str) -> str:
    from ...core.study import StudyStore, StudyStatus, default_metric_targets

    # Check for active study first (one task per session)
    with StudyStore() as _chk:
        active = _chk.get_active_study(session_id)
        if active is not None:
            return (
                f"Session already has an active study: {active.study_id[:12]}...\n"
                f"Status: {active.execution_status.value}\n"
                f"Cancel it first with /study cancel or wait for it to complete."
            )

    flags = _parse_study_flags(rest)
    # Objective = remaining positional tail (everything not consumed by flags)
    positional = [t for t in rest
                  if not (t.startswith("--") or _is_flag_value(rest, t))]
    objective = " ".join(positional).strip(' "\'') or "Research goal"
    ws = flags["workspace_path"] or _default_workspace()
    strategy = flags["strategy_name"]
    if not strategy:
        return (
            "/study start requires --strategy <name>. "
            "Use ``/study list strategies`` once we expose preset discovery."
        )
    targets = flags["metric_targets"] or default_metric_targets()

    try:
        from ...core.goal import GoalStore
        from ...core.goal.context import default_goal_criteria
        goal_store = GoalStore()
        goal = goal_store.replace_goal(
            session_id=session_id, objective=objective,
            criteria=default_goal_criteria(),
        )
        with StudyStore() as store:
            study = store.create_study(
                session_id=session_id, goal_id=goal.goal_id,
                objective=objective, workspace_path=ws, strategy_name=strategy,
                metric_targets=targets,
                budget_token=None, budget_turn=flags["budget_turn"],
                budget_time_seconds=flags["budget_time_seconds"],
                cooldown_base=30.0, cooldown_jitter=10.0, min_cooldown=1.0,
                max_rounds=flags["max_rounds"], behavior=flags["behavior"],
                monitor_interval_seconds=flags["monitor_interval_seconds"],
            )

        # Phase 3: Build GoalWorkflowConfig for the 9-agent preset
        from ...core.goal.workflow import (
            GoalWorkflowConfig, GoalWorkflowGoalConfig, GoalAgentConfig,
            CompletionConfig,
        )
        from pathlib import Path

        # Create a config that maps study parameters to the workflow
        agent_configs = [
            GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                           tools=["read_file", "list_history", "factor_analysis", "web_search",
                                  "read_url", "get_market_data", "search_symbol"],
                           input_from=[], evidence_criterion=0, timeout=180, max_retries=3),
            GoalAgentConfig(id="data_quality", prompt_file=".prompts/data_quality.md",
                           tools=["read_file", "web_search", "read_url", "get_market_data",
                                  "list_data_sources"],
                           input_from=["researcher"], evidence_criterion=1, timeout=120, max_retries=2),
            GoalAgentConfig(id="factor_analyst", prompt_file=".prompts/factor_analyst.md",
                           tools=["read_file", "compute_factor", "factor_analysis", "get_market_data"],
                           input_from=["researcher", "data_quality"], evidence_criterion=1,
                           timeout=180, max_retries=3),
            GoalAgentConfig(id="strategist", prompt_file=".prompts/strategist.md",
                           tools=["read_file", "write_file", "run_backtest", "git_diff",
                                  "web_search", "read_url", "get_market_data"],
                           input_from=["researcher", "data_quality", "factor_analyst"],
                           evidence_criterion=2, timeout=240, max_retries=3),
            GoalAgentConfig(id="portfolio_construction", prompt_file=".prompts/portfolio_construction.md",
                           tools=["read_file", "get_market_data"],
                           input_from=["strategist"], evidence_criterion=2, timeout=120, max_retries=2),
            GoalAgentConfig(id="backtest", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=[], input_from=["portfolio_construction"], evidence_criterion=2,
                           timeout=300, max_retries=1, executor_type="python_executor",
                           python_function="run_backtest_script"),
            GoalAgentConfig(id="risk_controller", prompt_file=".prompts/risk_controller.md",
                           tools=["read_file", "factor_analysis", "get_market_data"],
                           input_from=["backtest"], evidence_criterion=3, timeout=180, max_retries=2),
            GoalAgentConfig(id="attribution_analyst", prompt_file=".prompts/attribution_analyst.md",
                           tools=["read_file", "factor_analysis"],
                           input_from=["backtest", "risk_controller"], evidence_criterion=3,
                           timeout=180, max_retries=2),
            GoalAgentConfig(id="anti_overfit_analyst", prompt_file=".prompts/anti_overfit_analyst.md",
                           tools=["read_file", "list_history", "factor_analysis"],
                           input_from=["backtest", "risk_controller", "attribution_analyst"],
                           evidence_criterion=4, timeout=180, max_retries=2),
            GoalAgentConfig(id="backtest_diagnostics", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=["read_file", "run_backtest", "git_diff"],
                           input_from=["anti_overfit_analyst"], evidence_criterion=4,
                           timeout=120, max_retries=2),
            GoalAgentConfig(id="decide", prompt_file=".prompts/backtest_diagnostics.md",
                           tools=[], input_from=["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
                           evidence_criterion=4, timeout=60, max_retries=1,
                           executor_type="evaluator", python_function="decide"),
        ]

        config = GoalWorkflowConfig(
            name=f"autoresearch_{strategy}",
            description=f"9-agent autoresearch: {objective}",
            goal=GoalWorkflowGoalConfig(
                default_criteria=default_goal_criteria(),
                risk_tier="research_general",
            ),
            agents=agent_configs,
            dag={
                "researcher": [],
                "data_quality": ["researcher"],
                "factor_analyst": ["researcher", "data_quality"],
                "strategist": ["researcher", "data_quality", "factor_analyst"],
                "portfolio_construction": ["strategist"],
                "backtest": ["portfolio_construction"],
                "risk_controller": ["backtest"],
                "attribution_analyst": ["backtest", "risk_controller"],
                "anti_overfit_analyst": ["backtest", "risk_controller", "attribution_analyst"],
                "backtest_diagnostics": ["anti_overfit_analyst"],
                "decide": ["backtest", "anti_overfit_analyst", "backtest_diagnostics"],
            },
            completion=CompletionConfig(
                mode="auto",
                metric_targets=targets,
                monitor_interval_seconds=flags.get("monitor_interval_seconds"),
            ),
            budget_turn=flags.get("budget_turn"),
            budget_time_seconds=flags.get("budget_time_seconds"),
        )

        # Queue for async submission by _handle_study_command
        if flags["executor_type"] == "autoresearch":
            # AEGIS: autoresearch → scheduler → AutoresearchRunner (round-based)
            _study_pending_submits.append((study, None, goal.goal_id, objective, ws))
        else:
            # workflow → GoalWorkflowRunner (single DAG)
            _study_pending_submits.append((study, config, goal.goal_id, objective, ws))

    except ValueError as e:
        return f"Cannot create study: {e}"
    return (
        f"Study created: {study.study_id[:12]}...\n"
        f"Goal: {goal.goal_id[:12]}...\n"
        f"Objective: {study.objective}\n"
        f"Strategy: {study.strategy_name} @ {study.workspace_path}\n"
        f"Targets: {targets}\n"
        f"Status: {StudyStatus.QUEUED.value}"
    )


# Studies created by /study start need to be submitted to the scheduler on
# the FastAPI event loop. _handle_study_command awaits these after the
# dispatcher returns so the response text + the queued task both happen.
_study_pending_submits: list = []


async def _start_workflow_runner(
    config, session_id, goal_id, objective, workspace, session_service,
) -> None:
    """Start a GoalWorkflowRunner for a /study start command."""
    from ...core.goal.workflow import GoalWorkflowRunner
    from pathlib import Path

    runner = GoalWorkflowRunner(
        config=config,
        session_id=session_id,
        session_service=session_service,
        workspace=Path(workspace),
    )
    runner.set_goal_id(goal_id)
    await runner.start(objective)


def _is_flag_value(tokens, t) -> bool:
    """Return True if ``t`` follows a ``--flag`` token in ``tokens``."""
    i = tokens.index(t)
    return i > 0 and tokens[i - 1].startswith("--")


def _study_status_cmd(session_id: str) -> str:
    from .study import _get_study_scheduler  # for access consistency
    from ...core.study import StudyStore
    with StudyStore() as store:
        study = store.get_active_study(session_id)
    if study is None:
        return "No active study for this session. Use /study start ..."
    mon = (
        f"Monitor interval: {study.monitor_interval_seconds}s "
        f"last_check={study.last_monitor_check_at} "
        f"drift_count={study.monitor_drift_count}\n"
        if study.monitor_interval_seconds else ""
    )
    return (
        f"Study: {study.study_id[:12]}...\n"
        f"Objective: {study.objective}\n"
        f"Executor: {study.executor_type}\n"
        f"Status: {study.execution_status.value}\n"
        f"Round: {study.current_round}\n"
        f"Last metrics: {study.last_metrics}\n"
        f"Last verdict: {study.last_verdict}\n"
        f"Last error: {study.last_error}\n"
        f"{mon}"
    )


def _study_list_cmd(rest: list[str]) -> str:
    from ...core.study import StudyStore, StudyStatus
    status = None
    for tok in rest:
        if tok.startswith("status=") or tok.startswith("s="):
            val = tok.split("=", 1)[1]
            try:
                status = StudyStatus(val)
            except ValueError:
                return f"Invalid status: {val}"
    with StudyStore() as store:
        rows = store.list_studies(status=status, limit=20)
    if not rows:
        return "No studies found."
    out = [f"Found {len(rows)} study/studies (newest first):"]
    for r in rows:
        out.append(
            f"- {r.study_id[:12]}... [{r.execution_status.value}] "
            f"obj={r.objective[:40]} round={r.current_round}"
        )
    return "\n".join(out)


def _study_control_cmd(action: str, study_id: str) -> str:
    from .study import _get_study_scheduler
    sched = _get_study_scheduler()
    fn = {"pause": sched.pause, "resume": sched.resume,
          "cancel": sched.cancel}[action]
    if not fn(study_id):
        return f"Study {study_id} not found or not active — cannot {action}."
    return f"Study {study_id}: {action} requested."


def _study_redirect_cmd(study_id: str, directive: str, session_id: str) -> str:
    """Append a mid-execution directive to a study."""
    from ...core.study import StudyStore
    issued_by = f"chat:{session_id}"
    try:
        with StudyStore() as store:
            d = store.add_directive(
                study_id=study_id, content=directive,
                issued_by=issued_by,
            )
    except ValueError as e:
        return f"Cannot redirect: {e}"
    return (
        f"Directive recorded: {d.directive_id[:12]}...\n"
        f"Will apply to the next research round.\n"
        f"Content: {directive}"
    )


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


# ── /clear command handler (webui only) ─────────────────────────────


_HELP_TEXT = (
    "## 可用命令\n"
    "\n"
    "- `/goal <目标描述>` — 创建并跟踪一个复合目标\n"
    "- `/study <目标描述>` — 启动一个研究任务（多轮迭代）\n"
    "- `/compact` — 压缩当前会话的上下文\n"
    "- `/clear` — 清空当前会话的 LLM 上下文（保留历史消息）\n"
    "- `/help` — 显示本帮助\n"
    "\n"
    "## 快捷键\n"
    "\n"
    "- ⌘K — 搜索会话\n"
    "- ⌘P — 打开命令面板\n"
    "- ⌘T — 新建会话\n"
    "- ⌘B — 切换右栏\n"
    "- ⌘1–9 — 切换会话 tab\n"
    "- Enter — 发送 · Shift+Enter — 换行\n"
)


async def _handle_clear_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/clear`` — drop the LLM-visible history for this session.

    Implementation: calls ``MemoryManager.clear`` which truncates the
    session's memory backend rows (the buffer the AgentLoop reads at
    attempt start). The persisted message log (the ``messages`` table
    populated by the projector) is intentionally NOT touched so the
    user can still scroll their conversation. The UI sees a synthetic
    assistant acknowledgement via the same text-event flow as
    ``/compact``.
    """
    import uuid

    try:
        from strategy_research.core.agent.memory_manager import (
            get_default_memory_manager,
        )
        mm = get_default_memory_manager()
        await mm.clear(body.session_id)
        response_text = "✅ 已清空当前会话的上下文。历史消息保留可见。"
    except Exception as exc:
        logger.exception("clear failed")
        response_text = f"❌ 清空失败: {exc}"

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus
    text_id = str(uuid.uuid4())

    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": response_text,
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": text_id,
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


async def _handle_help_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/help`` — return the static cheat-sheet as an assistant message."""
    import uuid

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    service = _get_session_service()
    event_bus = service.event_bus
    text_id = str(uuid.uuid4())

    event_bus.emit(body.session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": body.content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(body.session_id, "text.started", {
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text_delta", {
        "text": _HELP_TEXT,
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "text.ended", {
        "text_id": text_id,
        "text": _HELP_TEXT,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(body.session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": _HELP_TEXT,
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
    import os
    _allow_shell = os.environ.get("SR_ALLOW_SHELL_TOOLS", "").lower() in ("1", "true", "yes")
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
        allow_shell_tools=_allow_shell,
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


@router.get("/attempts")
async def list_active_attempts(
    session_id: str = Query(...),
    request: Request = None,
):
    """Non-terminal attempts for a session (reload recovery).

    The frontend calls this after loading messages to rebuild its
    streaming/queued state: a page reload loses ``streamingMessageId``
    and the in-memory queued placeholders, and SSE replay cannot
    recover ``attempt.started`` / ``message_received`` (they sit before
    the Last-Event-ID cursor). See docs/streaming-reload-recovery.md.

    Returns ``{"attempts": [...]}`` with each entry:
    ``{attempt_id, message_id, status: "running"|"queued", prompt,
    created_at}`` — oldest first. Zombie rows (server restart) are
    filtered out by in-memory guards inside SessionService.
    """
    from .web_session import _fetch_session_owned, _get_db
    user_id = getattr(request.state, "user_id", "anonymous")
    _fetch_session_owned(_get_db(), session_id, user_id)
    service = _get_session_service()
    return {"attempts": service.list_active_attempts(session_id)}


@router.get("/personas")
async def list_personas(request: Request = None):
    """List available chat personas (roles) for the Composer agent selector.

    Returns ``{"personas": [{"id", "name", "description"}, ...]}`` — the
    curated set of role prompts the composer may switch to. ``chat`` is the
    default (general) persona.
    """
    from strategy_research.core.agent.prompt_builder import PromptBuilderFactory

    labels = {
        "chat": ("通用助手", "默认聊天助手，适合日常问答与策略讨论"),
        "researcher": ("研究员", "深度资料调研与信息检索"),
        "strategist": ("策略师", "策略设计与回测分析"),
        "factor_analyst": ("因子分析师", "因子计算与有效性分析"),
        "data_quality": ("数据质量", "数据质量与来源核查"),
        "portfolio_construction": ("组合构建", "组合权重与配置优化"),
        "risk_controller": ("风控", "风险度量与回撤控制"),
        "attribution_analyst": ("归因分析", "收益与风险归因"),
        "anti_overfit_analyst": ("反过拟合", "过拟合诊断与稳健性检验"),
        "backtest_diagnostics": ("回测诊断", "回测结果核查与诊断"),
        "critic": ("评审", "对结论与策略进行批判性审查"),
        "workflow_orchestrator": (
            "编排助手",
            "DAG 编排专用：把任务拆解为节点并通过 submit_dag_step 提交；固定用于 dag:{name} 会话",
        ),
    }
    persona = PromptBuilderFactory.list_roles()
    personas = []
    for pid in persona:
        name, desc = labels.get(pid, (pid, ""))
        personas.append({"id": pid, "name": name, "description": desc})
    return {"personas": personas}


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
