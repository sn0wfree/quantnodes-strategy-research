"""SSE event buffer with replay support — 5-minute window, max 10000 events。

Supports multicast: multiple listeners per session are notified independently.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


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
        with self._lock:
            self._counter += 1
            event_id = f"evt_{self._counter}"
            sse_event = SSEEvent(
                id=event_id,
                event=event,
                data=data,
                session_id=session_id,
            )
            self._buffer.append(sse_event)
        self._cleanup()
        # Notify ALL waiting SSE consumers for this session.
        # 同 event loop 线程：直接 evt.set() 即时唤醒 wait()（避免
        # call_soon_threadsafe 把 set 推到下一 tick，导致多个 push 合并
        # 成一次唤醒、_event_generator 一次性 yield 一批事件 → 前端"一
        # 下子全出现"）。跨线程（如 ThreadPool 里工具调 emit）：仍用
        # call_soon_threadsafe 跨线程安全唤醒。
        for evt in self._session_events.get(session_id, set()):
            try:
                try:
                    asyncio.get_running_loop()
                    evt.set()
                except RuntimeError:
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        # No current event loop (e.g. main thread detached
                        # after asyncio.set_event_loop(None)): Event.set()
                        # does not need a loop in py3.10+, just set.
                        loop = None
                    if loop is not None and loop.is_running():
                        loop.call_soon_threadsafe(evt.set)
                    else:
                        evt.set()
            except Exception:
                pass
        return event_id

    def replay_from(self, event_id: str, session_id: str) -> list[SSEEvent]:
        """Replay all events after the given event ID for a session.

        If the event_id is not found (evicted by TTL/capacity), falls back
        to returning the most recent events for the session.
        """
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
            if not found:
                # event_id was evicted — return recent events instead
                all_session = [e for e in self._buffer if e.session_id == session_id]
                return all_session[-200:]
            return events

    def get_events_since(self, session_id: str, last_id: str = "") -> list[SSEEvent]:
        """Get all events for a session after (or from) the given ID.

        If last_id is empty, returns recent events for the session
        (capped at 200 to avoid excessive first-connect replay).
        If last_id is not found (evicted), falls back to recent events.
        """
        with self._lock:
            if not last_id:
                # Return most recent events for this session (capped)
                all_session = [e for e in self._buffer if e.session_id == session_id]
                return all_session[-200:]
            events = []
            found = False
            for e in self._buffer:
                if e.session_id != session_id:
                    continue
                if found:
                    events.append(e)
                elif e.id == last_id:
                    found = True
            if not found:
                # last_id was evicted — fall back to recent events
                all_session = [e for e in self._buffer if e.session_id == session_id]
                return all_session[-200:]
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
