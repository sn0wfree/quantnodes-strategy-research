"""Chat API — send message + SSE event stream。"""

from __future__ import annotations

import json
import time
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .sse_buffer import sse_buffer

router = APIRouter()


class ChatMessage(BaseModel):
    session_id: str
    content: str
    images: Optional[list[str]] = None
    agent_id: Optional[str] = None


class SendMessageResponse(BaseModel):
    message_id: str
    event_id: str
    status: str = "queued"


@router.post("/send_async", response_model=SendMessageResponse)
async def send_async(body: ChatMessage, request: Request):
    """Send a message asynchronously. Returns message_id + event_id for confirmation."""
    import uuid
    message_id = str(uuid.uuid4())

    # Push initial event to buffer
    event_id = sse_buffer.push(
        event="message_received",
        data=json.dumps({
            "message_id": message_id,
            "session_id": body.session_id,
            "status": "queued",
        }),
        session_id=body.session_id,
    )

    return SendMessageResponse(
        message_id=message_id,
        event_id=event_id,
        status="queued",
    )


@router.post("/send")
async def send_sync(body: ChatMessage, request: Request):
    """Send a message synchronously (for non-streaming scenarios)."""
    import uuid
    message_id = str(uuid.uuid4())
    # Placeholder — actual LLM invocation will be added later
    return {
        "message_id": message_id,
        "reply": "收到消息（同步模式，LLM 集成待完成）",
    }


@router.get("/events")
async def chat_events(
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
    last_event_id: Optional[str] = Query(None, alias="Last-Event-ID"),
):
    """SSE event stream for a session.

    Supports Last-Event-ID header for replay on reconnection.
    """
    async def event_generator():
        # If last_event_id provided, replay missed events
        if last_event_id:
            missed = sse_buffer.replay_from(last_event_id, session_id)
            for evt in missed:
                yield {
                    "event": evt.event,
                    "data": evt.data,
                    "id": evt.id,
                }

        # Keep connection alive with heartbeat every 15s
        event_id = 0
        while True:
            await asyncio.sleep(15)
            event_id += 1
            yield {
                "event": "heartbeat",
                "data": json.dumps({"ts": time.time()}),
                "id": f"hb_{event_id}",
            }

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
