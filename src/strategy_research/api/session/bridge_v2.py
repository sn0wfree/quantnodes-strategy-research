"""Bridge: forward EventStore events to the legacy SSEEventBuffer.

Phase 7+8 replacement for the EventBus → SSEEventBuffer bridge. Now uses
EventStore.sse_pusher callback (single integration point).

The legacy ``EventBus`` + ``EventBusV2`` classes in ``events.py`` /
``event_bus_v2.py`` are kept for backward compatibility but new code should
use ``EventStore`` (Phase 7+8).

This bridge lets the new EventStore coexist with the existing FastAPI SSE
endpoint (which uses SSEEventBuffer) until that endpoint is migrated.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_bridge_attached = False


def attach_eventstore_to_sse(event_store) -> None:
    """Attach EventStore → SSEEventBuffer bridge (idempotent).

    After calling this, every ``EventStore.emit()`` will also push to the
    legacy SSEEventBuffer (``api.sse_buffer.sse_buffer``), so the existing
    ``/api/chat/events`` endpoint keeps working.

    Args:
        event_store: EventStore instance (from ``get_default_event_store()``).
    """
    global _bridge_attached
    if _bridge_attached:
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
    _bridge_attached = True


__all__ = ["attach_eventstore_to_sse"]
