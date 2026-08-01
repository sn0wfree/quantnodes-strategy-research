"""Bridge: forward EventBus events to the legacy SSEEventBuffer.

This lets the new SessionService (which uses EventBus) coexist with the
existing FastAPI SSE endpoint (which uses SSEEventBuffer) until the SSE
endpoint is migrated to use EventBus directly.

The bridge is intentionally a no-op when the buffer isn't imported, so it
can be safely used from anywhere in the API stack.
"""

from __future__ import annotations

import json
import logging

from .events import EventBus, SSEEvent

logger = logging.getLogger(__name__)

_bridge_attached = False


def attach_eventbus_to_sse(event_bus: EventBus) -> None:
    """Attach EventBus → SSEEventBuffer bridge (idempotent).

    After calling this, every EventBus.emit() will also push to the legacy
    SSEEventBuffer (``api.sse_buffer.sse_buffer``), so the existing
    ``/api/chat/events`` endpoint keeps working.

    Args:
        event_bus: The shared EventBus instance used by SessionService.
    """
    global _bridge_attached
    if _bridge_attached:
        return

    try:
        from ..sse_buffer import sse_buffer
    except ImportError:
        logger.warning("SSEEventBuffer not available; bridge not attached")
        return

    original_publish = event_bus.publish

    def bridged_publish(event: SSEEvent) -> None:
        # 1) Original EventBus publish (buffered, multi-subscriber)
        original_publish(event)
        # 2) Mirror to SSEEventBuffer for the FastAPI SSE endpoint
        try:
            sse_buffer.push(
                event.event_type,
                json.dumps(event.data, ensure_ascii=False),
                event.session_id,
            )
        except Exception as exc:
            logger.warning("SSEEventBuffer mirror failed: %s", exc)

    event_bus.publish = bridged_publish
    _bridge_attached = True


__all__ = ["attach_eventbus_to_sse"]
