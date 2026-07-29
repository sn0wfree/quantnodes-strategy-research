"""Chat API — send message + SSE event stream with real LLM integration。"""

from __future__ import annotations

import json
import time
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..sse_buffer import sse_buffer
from ..session.bridge import attach_eventbus_to_sse
from ..session.events import EventBus
from ..session.service import SessionService
from ..session.store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared SessionService (singleton) ─────────────────────────────────────
# Borrowed from vibe_trading: one service for the whole process, with an
# EventBus that mirrors events to the legacy SSEEventBuffer for the existing
# FastAPI SSE endpoint.

_event_bus = EventBus()
attach_eventbus_to_sse(_event_bus)


def _get_session_service() -> SessionService:
    """Return the singleton SessionService bound to the active DB."""
    from .web_session import _get_db_path

    db_path = _get_db_path()
    store = SessionStore(db_path=db_path)
    return SessionService(store=store, event_bus=_event_bus)


# Per-session LLM loop tasks (so we can cancel on new message)
_loop_tasks: dict[str, asyncio.Task] = {}

# Per-session conversation history (legacy in-memory cache; replaced by DB)
_session_histories: dict[str, list[dict[str, Any]]] = {}


def _get_or_create_history(session_id: str) -> list[dict[str, Any]]:
    """Get or create conversation history for a session."""
    if session_id not in _session_histories:
        _session_histories[session_id] = []
    return _session_histories[session_id]


def _build_llm_config():
    """Build LLMConfig from env/settings. Same logic as TUI's __main__.py."""
    try:
        from dotenv import load_dotenv
        from pathlib import Path
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


def _get_system_prompt() -> str:
    """Load the chat system prompt."""
    prompt_path = Path(__file__).parent.parent.parent.parent / "templates" / ".prompts" / "chat.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback: try absolute path
    try:
        from strategy_research.cli.tui import _CHAT_PROMPT_PATH
        return _CHAT_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return "你是 QuantNodes-Research 的量化金融助手。用自然语言回复，简洁直接。"


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


async def _run_agent_loop_background(
    session_id: str,
    message_id: str,
    task: str,
):
    """Run AgentLoop in background, pushing events to SSE buffer.

    This mirrors TUI's ChatSession._run_agent_loop() but pushes events
    to sse_buffer instead of Textual widgets.

    Test mode: when ``STRATEGY_RESEARCH_TEST_CHAT=1``, emits a scripted
    sequence of SSE events instead of calling the real LLM. Used by
    E2E tests to avoid network calls and ensure determinism.

    Persistence: user message is inserted before the loop runs, assistant
    message (with accumulated parts) is inserted after agent_done.
    """
    import os

    # ── Persist user message + auto-title ────────────────────────────────
    from .web_session import persist_message, auto_title_session, _get_db
    persist_message(
        session_id=session_id,
        role="user",
        content=task,
    )
    new_title = auto_title_session(session_id, task)
    if new_title:
        # Notify frontend of the auto-title update via SSE
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

    # Accumulator for assistant parts (text + tool calls + thinking)
    accumulated_parts: list[dict[str, Any]] = []

    if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
        await _run_test_script(session_id, message_id, task, accumulated_parts)
        # Persist assistant message after scripted run
        assistant_content = "".join(
            p.get("text", "") for p in accumulated_parts if p.get("type") == "text"
        )
        persist_message(
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            parts=accumulated_parts or None,
            metadata={"model": "test-script"},
            message_id=message_id,
        )
        return

    cfg = _build_llm_config()
    if cfg is None or not cfg.api_key:
        err_data = {"message_id": message_id, "error": "LLM 未配置。请设置 OPENAI_API_KEY 环境变量。"}
        sse_buffer.push("error", json.dumps(err_data), session_id)
        sse_buffer.push(
            "agent_done",
            json.dumps({"message_id": message_id, "status": "error"}),
            session_id,
        )
        # Persist error message
        persist_message(
            session_id=session_id,
            role="assistant",
            content=err_data["error"],
            parts=[{"type": "error", "message": err_data["error"]}],
            message_id=message_id,
        )
        return

    # Build on_event callback that pushes to sse_buffer AND accumulates parts
    def on_event(event_type: str, data: dict):
        # Add message_id to every event for frontend correlation
        event_data = {**data, "message_id": message_id}
        sse_buffer.push(event_type, json.dumps(event_data, ensure_ascii=False), session_id)

        # Accumulate for persistence
        if event_type == "text_delta":
            text = event_data.get("text", "")
            if accumulated_parts and accumulated_parts[-1].get("type") == "text":
                accumulated_parts[-1]["text"] += text
            else:
                accumulated_parts.append({"type": "text", "text": text})
        elif event_type == "tool_call":
            accumulated_parts.append({
                "type": "tool_call",
                "id": event_data.get("id"),
                "name": event_data.get("name"),
                "arguments": event_data.get("arguments"),
            })
        elif event_type == "tool_result":
            # Attach result to last tool_call
            for p in reversed(accumulated_parts):
                if p.get("type") == "tool_call" and p.get("id") == event_data.get("id"):
                    p["result"] = event_data.get("result")
                    p["status"] = event_data.get("status", "done")
                    break
        elif event_type == "tool_progress":
            # Attach progress steps to matching tool_call
            steps = event_data.get("steps", [])
            for p in reversed(accumulated_parts):
                if p.get("type") == "tool_call" and p.get("id") == event_data.get("id"):
                    p["progress"] = steps
                    break
        elif event_type == "thinking_delta":
            delta = event_data.get("delta", "")
            if accumulated_parts and accumulated_parts[-1].get("type") == "thinking":
                accumulated_parts[-1]["text"] += delta
            else:
                accumulated_parts.append({"type": "thinking", "text": delta})
        elif event_type == "thinking_done":
            if accumulated_parts and accumulated_parts[-1].get("type") == "thinking":
                accumulated_parts[-1]["status"] = "done"

    try:
        from strategy_research.core.agent.loop import AgentLoop
        from strategy_research.core.agent.builtin_tools import build_default_registry

        try:
            registry = build_default_registry()
        except Exception:
            registry = None

        system_prompt = _get_system_prompt()
        history = _get_or_create_history(session_id)

        loop = AgentLoop(
            config=cfg,
            registry=registry,
            workspace=None,
            on_event=on_event,
            stream_mode=True,
            max_iterations=1,  # chat: single pass
            session_id=session_id,
            system_prompt=system_prompt,
            allowed_tools=[],  # chat-only: no tools
        )

        # Run the loop
        result = await loop.arun(task)

        # Append to history
        if result.answer:
            history.append({"role": "user", "content": task})
            history.append({"role": "assistant", "content": result.answer})

        # Signal completion
        sse_buffer.push(
            "agent_done",
            json.dumps({
                "message_id": message_id,
                "status": "success" if result.answer else "empty",
                "answer_length": len(result.answer) if result.answer else 0,
            }),
            session_id,
        )

        # Persist assistant message (with all accumulated parts)
        assistant_content = result.answer or "".join(
            p.get("text", "") for p in accumulated_parts if p.get("type") == "text"
        )
        persist_message(
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            parts=accumulated_parts or None,
            metadata={"model": getattr(cfg, "model", None)} if cfg else None,
            message_id=message_id,
        )

    except Exception as exc:
        logger.error("AgentLoop failed for session %s: %s", session_id, exc, exc_info=True)
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
        # Persist error as assistant message
        persist_message(
            session_id=session_id,
            role="assistant",
            content=f"[error] {exc}",
            parts=[{"type": "error", "message": str(exc)}],
            message_id=message_id,
        )


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
    """Send a message asynchronously. Spawns AgentLoop in background.

    Returns message_id + event_id for confirmation.
    Frontend should listen to /events SSE stream for real-time response.

    Uses the unified SessionService (also shared with TUI). The Attempt's
    message_id is what SSE events carry for correlation; the user message
    gets its own auto-generated UUID (see chat.py history for the
    loadMessages-key-collision bug this prevents).
    """
    # Cancel any existing loop for this session (new message replaces old)
    if body.session_id in _loop_tasks:
        _loop_tasks[body.session_id].cancel()

    # Delegate to SessionService — it handles DB persistence, AgentLoop
    # execution, history context, and event emission.
    service = _get_session_service()
    result = await service.send_message(
        session_id=body.session_id,
        content=body.content,
    )

    # Track the spawned attempt task so we can cancel future messages
    for task in service._active_loops.values():
        _loop_tasks[body.session_id] = task
        break

    return SendMessageResponse(
        message_id=result["user_message_id"],
        user_message_id=result["user_message_id"],
        assistant_message_id=result["assistant_message_id"],
        event_id="",
        status="processing",
        attempt_id=result.get("attempt_id"),
    )


class CancelRequest(BaseModel):
    session_id: str
    attempt_id: Optional[str] = None


@router.post("/cancel")
async def cancel_attempt(body: CancelRequest):
    """Cancel an in-flight agent attempt for a session.

    If attempt_id is provided, cancels that specific attempt.
    Otherwise, cancels any active attempt for the session.
    """
    # Cancel via loop task tracking (works without attempt_id)
    task = _loop_tasks.pop(body.session_id, None)
    if task is not None:
        task.cancel()
        return {"status": "cancelled", "session_id": body.session_id}

    # Fallback: try SessionService.cancel with attempt_id
    if body.attempt_id:
        service = _get_session_service()
        ok = service.cancel(body.attempt_id)
        if ok:
            return {"status": "cancelled", "attempt_id": body.attempt_id}

    return {"status": "no_active_attempt", "session_id": body.session_id}


@router.post("/send")
async def send_sync(body: ChatMessage, request: Request):
    """Send a message synchronously (non-streaming).

    Waits for the full response. Use /send_async for streaming.
    """
    cfg = _build_llm_config()
    if cfg is None or not cfg.api_key:
        raise HTTPException(status_code=503, detail="LLM 未配置")

    from strategy_research.core.agent.loop import AgentLoop
    from strategy_research.core.agent.builtin_tools import build_default_registry

    try:
        registry = build_default_registry()
    except Exception:
        registry = None

    system_prompt = _get_system_prompt()

    loop = AgentLoop(
        config=cfg,
        registry=registry,
        workspace=None,
        on_event=None,  # no streaming in sync mode
        stream_mode=False,
        max_iterations=1,
        session_id=body.session_id,
        system_prompt=system_prompt,
        allowed_tools=[],
    )

    result = await loop.arun(body.content)
    return {
        "message_id": str(uuid.uuid4()),
        "reply": result.answer or "",
    }


@router.get("/events")
async def chat_events(
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
    last_event_id: Optional[str] = Query(None, alias="Last-Event-ID"),
):
    """SSE event stream for a session.

    Streams real-time events from AgentLoop (text_delta, tool_call, etc.)
    Supports Last-Event-ID header for replay on reconnection.
    """
    # Register for async notifications
    notification_event = sse_buffer.register_session(session_id)

    async def event_generator():
        last_id = last_event_id or ""

        try:
            # Replay missed events first
            if last_id:
                missed = sse_buffer.replay_from(last_id, session_id)
                for evt in missed:
                    yield _format_sse(evt)
                    last_id = evt.id

            # Flush any events that arrived before we started listening
            existing = sse_buffer.get_events_since(session_id, last_id)
            for evt in existing:
                yield _format_sse(evt)
                last_id = evt.id

            # Stream new events
            event_count = 0
            while True:
                # Wait for new events (with timeout for heartbeat)
                try:
                    await asyncio.wait_for(notification_event.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send heartbeat
                    event_count += 1
                    yield _heartbeat_sse(event_count)
                    continue

                # Clear the event and get new events
                notification_event.clear()
                new_events = sse_buffer.get_events_since(session_id, last_id)
                for evt in new_events:
                    yield _format_sse(evt)
                    last_id = evt.id

        finally:
            sse_buffer.unregister_session(session_id, notification_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    """Format a heartbeat SSE event."""
    return f"id: hb_{count}\nevent: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
