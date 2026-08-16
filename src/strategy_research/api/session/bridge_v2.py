"""Bridge: forward EventStore events to the legacy SSEEventBuffer.

EventStore is the single event source. This bridge lets the
``/api/chat/events`` FastAPI endpoint (which reads from SSEEventBuffer)
keep working until that endpoint is migrated to read directly from
EventStore.subscribe().

Idempotent per EventStore instance via the ``_sse_bridge_attached``
attribute.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

def attach_eventstore_to_sse(event_store) -> None:
    """Attach EventStore → SSEEventBuffer bridge (idempotent per instance).

    After calling this, every ``EventStore.emit()`` will also push to the
    legacy SSEEventBuffer (``api.sse_buffer.sse_buffer``), so the existing
    ``/api/chat/events`` endpoint keeps working.

    Idempotence is tracked per EventStore instance (attribute
    ``_sse_bridge_attached``) — NOT via a module global — because the app
    creates multiple EventStore instances (e.g. a module-level one at
    import + one per SessionService via _get_session_service). A module
    global would attach to only the first instance and leave the real
    SessionService EventStore without an sse_pusher, breaking live SSE.

    Args:
        event_store: EventStore instance to attach the bridge to.
    """
    if getattr(event_store, "_sse_bridge_attached", False):
        return

    try:
        from ..sse_buffer import sse_buffer
    except ImportError:
        logger.warning("SSEEventBuffer not available; bridge not attached")
        return

    # Replace sse_pusher with a wrapped version that also pushes to SSEEventBuffer
    original_pusher = event_store._sse_pusher

    def wrapped_pusher(session_id: str, event) -> None:
        # 1) Original push (if any)
        if original_pusher:
            try:
                original_pusher(session_id, event)
            except Exception:
                pass
        # 2) Mirror to SSEEventBuffer
        try:
            data = (
                json.dumps(event.data, ensure_ascii=False)
                if hasattr(event, "data")
                else json.dumps({})
            )
            sse_buffer.push(event.type, data, session_id)
        except Exception as exc:
            logger.warning("SSEEventBuffer mirror failed: %s", exc)

    event_store._sse_pusher = wrapped_pusher
    event_store._sse_bridge_attached = True


__all__ = ["attach_eventstore_to_sse"]
