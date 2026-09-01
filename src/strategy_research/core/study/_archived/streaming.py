"""Streaming — True streaming output for study execution.

Provides real-time streaming of study execution events, replacing the
batch SSE approach with a true stream=True capability.

Features:
- Async iterator for streaming events
- Event buffering and batching
- Backpressure handling
- Client-side stream consumption

Inspired by CrewAI's streaming=True and SSE protocol.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator
from uuid import uuid4

logger = logging.getLogger(__name__)


class StreamEventType(str, Enum):
    """Types of stream events."""
    STUDY_STARTED = "study_started"
    STUDY_PAUSED = "study_paused"
    STUDY_RESUMED = "study_resumed"
    STUDY_COMPLETED = "study_completed"
    STUDY_CANCELLED = "study_cancelled"
    STUDY_ERROR = "study_error"

    ROUND_STARTED = "round_started"
    ROUND_COMPLETED = "round_completed"
    ROUND_METRICS = "round_metrics"

    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"

    AGENT_OUTPUT = "agent_output"
    BACKTEST_PROGRESS = "backtest_progress"

    TOKEN = "token"  # For streaming LLM output
    DONE = "done"
    ERROR = "error"


@dataclass
class StreamEvent:
    """A single stream event."""
    event_id: str
    event_type: StreamEventType
    study_id: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "study_id": self.study_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "seq": self.seq,
        }

    def to_sse(self) -> str:
        """Convert to SSE format."""
        import json
        data = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        return f"event: {self.event_type.value}\ndata: {data}\n\n"


class StreamBuffer:
    """Buffer for streaming events with backpressure."""

    def __init__(self, max_size: int = 1000, flush_interval: float = 0.1):
        self._buffer: list[StreamEvent] = []
        self._max_size = max_size
        self._flush_interval = flush_interval
        self._subscribers: dict[str, asyncio.Queue[StreamEvent]] = {}
        self._lock = asyncio.Lock()
        self._last_flush = time.time()

    async def push(self, event: StreamEvent) -> None:
        """Push an event to the buffer."""
        async with self._lock:
            self._buffer.append(event)

            # Notify all subscribers
            for subscriber_queue in self._subscribers.values():
                try:
                    subscriber_queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # Drop if subscriber is too slow

            # Flush if buffer is full or enough time has passed
            now = time.time()
            if (
                len(self._buffer) >= self._max_size
                or now - self._last_flush >= self._flush_interval
            ):
                self._last_flush = now

    def subscribe(self) -> tuple[str, asyncio.Queue[StreamEvent]]:
        """Subscribe to stream events."""
        subscriber_id = str(uuid4())
        queue = asyncio.Queue(maxsize=self._max_size)
        self._subscribers[subscriber_id] = queue
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        """Unsubscribe from stream events."""
        self._subscribers.pop(subscriber_id, None)

    def get_buffered(self) -> list[StreamEvent]:
        """Get all buffered events."""
        return list(self._buffer)


class StudyStream:
    """Streaming interface for a study execution."""

    def __init__(self, study_id: str, buffer: StreamBuffer | None = None):
        self.study_id = study_id
        self._buffer = buffer or StreamBuffer()
        self._subscriber_id: str | None = None
        self._queue: asyncio.Queue[StreamEvent] | None = None
        self._seq = 0

    async def start(self) -> None:
        """Start streaming."""
        self._subscriber_id, self._queue = self._buffer.subscribe()

    async def stop(self) -> None:
        """Stop streaming."""
        if self._subscriber_id:
            self._buffer.unsubscribe(self._subscriber_id)
            self._subscriber_id = None
            self._queue = None

    async def emit(
        self,
        event_type: StreamEventType,
        data: dict[str, Any] | None = None,
    ) -> StreamEvent:
        """Emit a stream event."""
        self._seq += 1
        event = StreamEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            study_id=self.study_id,
            timestamp=time.time(),
            data=data or {},
            seq=self._seq,
        )
        await self._buffer.push(event)
        return event

    async def iter_events(
        self,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async iterator for streaming events."""
        if not self._queue:
            await self.start()

        while True:
            try:
                if timeout:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=timeout,
                    )
                else:
                    event = await self._queue.get()

                if event.event_type == StreamEventType.DONE:
                    break

                yield event

            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                break

    async def iter_tokens(self) -> AsyncIterator[str]:
        """Async iterator for streaming LLM tokens."""
        async for event in self.iter_events():
            if event.event_type == StreamEventType.TOKEN:
                yield event.data.get("token", "")
            elif event.event_type == StreamEventType.DONE:
                break

    async def collect(self) -> list[StreamEvent]:
        """Collect all events into a list."""
        events = []
        async for event in self.iter_events():
            events.append(event)
        return events


class StreamingEmitter:
    """Emits streaming events for study execution."""

    def __init__(self, buffer: StreamBuffer | None = None):
        self._buffer = buffer or StreamBuffer()
        self._streams: dict[str, StudyStream] = {}

    def get_stream(self, study_id: str) -> StudyStream:
        """Get or create a stream for a study."""
        if study_id not in self._streams:
            self._streams[study_id] = StudyStream(study_id, self._buffer)
        return self._streams[study_id]

    async def emit_study_started(self, study_id: str, data: dict | None = None) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.STUDY_STARTED, data)

    async def emit_study_completed(self, study_id: str, data: dict | None = None) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.STUDY_COMPLETED, data)
        await stream.emit(StreamEventType.DONE)

    async def emit_round_started(self, study_id: str, round_num: int, data: dict | None = None) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.ROUND_STARTED, {"round": round_num, **(data or {})})

    async def emit_round_completed(
        self,
        study_id: str,
        round_num: int,
        metrics: dict | None = None,
        verdict: str | None = None,
    ) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.ROUND_COMPLETED, {
            "round": round_num,
            "metrics": metrics,
            "verdict": verdict,
        })

    async def emit_agent_output(
        self,
        study_id: str,
        agent_name: str,
        output: str,
    ) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.AGENT_OUTPUT, {
            "agent": agent_name,
            "output": output,
        })

    async def emit_token(self, study_id: str, token: str) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.TOKEN, {"token": token})

    async def emit_error(self, study_id: str, error: str) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.ERROR, {"error": error})

    async def emit_done(self, study_id: str) -> None:
        stream = self.get_stream(study_id)
        await stream.emit(StreamEventType.DONE)


# Global streaming infrastructure
_global_buffer: StreamBuffer | None = None
_global_emitter: StreamingEmitter | None = None
_global_lock = asyncio.Lock()


async def get_streaming_emitter() -> StreamingEmitter:
    """Get or create the global streaming emitter."""
    global _global_buffer, _global_emitter
    async with _global_lock:
        if _global_buffer is None:
            _global_buffer = StreamBuffer()
            _global_emitter = StreamingEmitter(_global_buffer)
        return _global_emitter


async def reset_streaming() -> None:
    """Reset the global streaming infrastructure (for testing)."""
    global _global_buffer, _global_emitter
    async with _global_lock:
        _global_buffer = None
        _global_emitter = None
