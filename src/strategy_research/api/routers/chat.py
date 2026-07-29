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

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-session LLM loop tasks (so we can cancel on new message)
_loop_tasks: dict[str, asyncio.Task] = {}

# Per-session conversation history
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
    event_id: str
    status: str = "queued"


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
    """
    import os

    if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
        await _run_test_script(session_id, message_id, task)
        return

    cfg = _build_llm_config()
    if cfg is None or not cfg.api_key:
        sse_buffer.push(
            "error",
            json.dumps({"message_id": message_id, "error": "LLM 未配置。请设置 OPENAI_API_KEY 环境变量。"}),
            session_id,
        )
        sse_buffer.push(
            "agent_done",
            json.dumps({"message_id": message_id, "status": "error"}),
            session_id,
        )
        return

    # Build on_event callback that pushes to sse_buffer
    def on_event(event_type: str, data: dict):
        # Add message_id to every event for frontend correlation
        event_data = {**data, "message_id": message_id}
        sse_buffer.push(event_type, json.dumps(event_data, ensure_ascii=False), session_id)

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


async def _run_test_script(session_id: str, message_id: str, task: str):
    """Emit a scripted sequence of SSE events for E2E testing.

    Mirrors what the real AgentLoop would emit:
    - thinking_start → thinking_delta (×N) → (collapse)
    - text_delta (×N, simulating streaming response)
    - agent_done

    Events are spaced 30ms apart to keep tests fast but realistic.
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
    try:
        # Phase 1: thinking block (collapsed by default)
        sse_buffer.push(
            "thinking_start",
            json.dumps({"message_id": message_id}, ensure_ascii=False),
            session_id,
        )
        thinking_chunks = ["分析用户问题", " → 检索相关策略", " → 准备回复"]
        for chunk in thinking_chunks:
            await asyncio.sleep(0.03)
            sse_buffer.push(
                "thinking_delta",
                json.dumps({"message_id": message_id, "delta": chunk}, ensure_ascii=False),
                session_id,
            )
        sse_buffer.push(
            "thinking_done",
            json.dumps({"message_id": message_id}, ensure_ascii=False),
            session_id,
        )

        # Phase 2: streaming text reply
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
    """
    message_id = str(uuid.uuid4())

    # Cancel any existing loop for this session (new message replaces old)
    if body.session_id in _loop_tasks:
        _loop_tasks[body.session_id].cancel()

    # Push confirmation event
    event_id = sse_buffer.push(
        "message_received",
        json.dumps({
            "message_id": message_id,
            "session_id": body.session_id,
            "status": "processing",
        }),
        body.session_id,
    )

    # Spawn AgentLoop in background
    task = asyncio.create_task(
        _run_agent_loop_background(body.session_id, message_id, body.content)
    )
    _loop_tasks[body.session_id] = task

    return SendMessageResponse(
        message_id=message_id,
        event_id=event_id,
        status="processing",
    )


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
