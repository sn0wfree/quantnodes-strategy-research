"""SSE event buffer with replay support — 5-minute window, max 10000 events。

Supports multicast: multiple listeners per session are notified independently.
"""

from __future__ import annotations

import asyncio
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SSEEvent:
    """A single SSE event with ID for replay."""
    id: str
    event: str
    data: str
    session_id: str
    timestamp: float = field(default_factory=time.time)


class SSEEventBuffer:
    """Thread-safe ring buffer for SSE events with replay support.

    Events are stored for 5 minutes or max 10000 events.
    Clients can request replay from a specific event ID.
    Supports async notification via asyncio.Event per session.

    Multicast: multiple listeners per session are supported.
    Each register_session() call returns a unique asyncio.Event.
    push() notifies ALL registered events for a session.
    """

    def __init__(self, max_events: int = 10000, ttl_seconds: float = 300):
        self.max_events = max_events
        self.ttl_seconds = ttl_seconds
        self._buffer: deque[SSEEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._counter = 0
        # Per-session async notification events (multicast: set of events)
        self._session_events: dict[str, set[asyncio.Event]] = defaultdict(set)

    def push(self, event: str, data: str, session_id: str) -> str:
        """Push a new event. Returns the event ID.

        Notifies ALL registered listeners for the session (multicast).
        """
        self._counter += 1
        event_id = f"evt_{self._counter}"
        sse_event = SSEEvent(
            id=event_id,
            event=event,
            data=data,
            session_id=session_id,
        )
        with self._lock:
            self._buffer.append(sse_event)
        self._cleanup()
        # Notify ALL waiting SSE consumers for this session
        for evt in self._session_events.get(session_id, set()):
            try:
                evt.set()
            except RuntimeError:
                pass
        return event_id

    def replay_from(self, event_id: str, session_id: str) -> list[SSEEvent]:
        """Replay all events after the given event ID for a session."""
        with self._lock:
            events = []
            found = False
            for e in self._buffer:
                if e.session_id != session_id:
                    continue
                if found:
                    events.append(e)
                elif e.id == event_id:
                    found = True
            return events

    def get_events_since(self, session_id: str, last_id: str = "") -> list[SSEEvent]:
        """Get all events for a session after (or from) the given ID.

        If last_id is empty, returns all recent events for the session.
        """
        with self._lock:
            if not last_id:
                # Return all recent events for this session
                return [e for e in self._buffer if e.session_id == session_id]
            events = []
            found = False
            for e in self._buffer:
                if e.session_id != session_id:
                    continue
                if found:
                    events.append(e)
                elif e.id == last_id:
                    found = True
            return events

    def register_session(self, session_id: str) -> asyncio.Event:
        """Register an async notification event for a session.

        Returns a unique asyncio.Event. Multiple calls for the same session
        create multiple independent events (multicast support).
        """
        evt = asyncio.Event()
        self._session_events[session_id].add(evt)
        return evt

    def unregister_session(self, session_id: str, event: asyncio.Event):
        """Unregister a specific notification event for a session.

        Removes only the given event. Other listeners for the same session
        are not affected.
        """
        events = self._session_events.get(session_id)
        if events:
            events.discard(event)
            if not events:
                del self._session_events[session_id]

    def _cleanup(self):
        """Remove expired events."""
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()


# Global singleton
sse_buffer = SSEEventBuffer()
