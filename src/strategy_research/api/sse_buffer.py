"""SSE event buffer with replay support — 5-minute window, max 10000 events。"""

from __future__ import annotations

import time
import threading
from collections import deque
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
    """

    def __init__(self, max_events: int = 10000, ttl_seconds: float = 300):
        self.max_events = max_events
        self.ttl_seconds = ttl_seconds
        self._buffer: deque[SSEEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._counter = 0

    def push(self, event: str, data: str, session_id: str) -> str:
        """Push a new event. Returns the event ID."""
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

    def _cleanup(self):
        """Remove expired events."""
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            while self._buffer and self._buffer[0].timestamp < cutoff:
                self._buffer.popleft()


# Global singleton
sse_buffer = SSEEventBuffer()
