"""Signal/Timer — External signal injection and timer capabilities.

Inspired by Temporal's Signal and Timer mechanisms:
- Signal: External events that can pause/resume/modify running workflows
- Timer: Delayed execution within workflows

This enables:
- External control (pause, resume, cancel, inject directives)
- Scheduled actions (delayed checks, periodic monitoring)
- Event-driven workflow modifications
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine
from uuid import uuid4

logger = logging.getLogger(__name__)


class SignalType(str, Enum):
    """Types of signals that can be sent to a study."""
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    INJECT_DIRECTIVE = "inject_directive"
    UPDATE_BUDGET = "update_budget"
    UPDATE_TARGETS = "update_targets"
    FORCE_STOP = "force_stop"
    CUSTOM = "custom"


class TimerStatus(str, Enum):
    """Timer status."""
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


@dataclass
class Signal:
    """External signal sent to a running study."""
    signal_id: str
    signal_type: SignalType
    study_id: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "external"  # "external", "system", "scheduler"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type.value,
            "study_id": self.study_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "source": self.source,
        }


@dataclass
class Timer:
    """Delayed execution timer."""
    timer_id: str
    study_id: str
    delay_seconds: float
    callback_name: str
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    fired_at: float | None = None
    status: TimerStatus = TimerStatus.PENDING

    @property
    def fire_at(self) -> float:
        return self.created_at + self.delay_seconds

    @property
    def remaining(self) -> float:
        if self.status != TimerStatus.PENDING:
            return 0.0
        return max(0.0, self.fire_at - time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "timer_id": self.timer_id,
            "study_id": self.study_id,
            "delay_seconds": self.delay_seconds,
            "callback_name": self.callback_name,
            "data": self.data,
            "created_at": self.created_at,
            "fired_at": self.fired_at,
            "status": self.status.value,
        }


class SignalHandler:
    """Protocol for handling signals."""

    async def handle_signal(self, signal: Signal) -> Any:
        """Handle a signal."""
        ...


class TimerCallback:
    """Protocol for timer callbacks."""

    async def on_timer_fired(self, timer: Timer) -> Any:
        """Called when a timer fires."""
        ...


class SignalRegistry:
    """Registry for signal handlers."""

    def __init__(self):
        self._handlers: dict[SignalType, list[SignalHandler]] = {}

    def register(self, signal_type: SignalType, handler: SignalHandler) -> None:
        """Register a handler for a signal type."""
        if signal_type not in self._handlers:
            self._handlers[signal_type] = []
        self._handlers[signal_type].append(handler)

    def get_handlers(self, signal_type: SignalType) -> list[SignalHandler]:
        """Get handlers for a signal type."""
        return self._handlers.get(signal_type, [])


class TimerRegistry:
    """Registry for timer callbacks."""

    def __init__(self):
        self._callbacks: dict[str, TimerCallback] = {}

    def register(self, callback_name: str, callback: TimerCallback) -> None:
        """Register a timer callback."""
        self._callbacks[callback_name] = callback

    def get_callback(self, callback_name: str) -> TimerCallback | None:
        """Get a timer callback by name."""
        return self._callbacks.get(callback_name)


class SignalManager:
    """Manages signals and timers for studies.

    Provides:
    - Signal sending and handling
    - Timer creation and firing
    - Signal/Timer event recording
    """

    def __init__(
        self,
        event_store: Any | None = None,
        signal_registry: SignalRegistry | None = None,
        timer_registry: TimerRegistry | None = None,
    ):
        self._event_store = event_store
        self._signal_registry = signal_registry or SignalRegistry()
        self._timer_registry = timer_registry or TimerRegistry()
        self._pending_signals: dict[str, Signal] = {}
        self._pending_timers: dict[str, Timer] = {}
        self._fired_timers: dict[str, Timer] = {}
        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._timer_tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """Start the signal manager."""
        self._running = True
        asyncio.create_task(self._process_signals())
        logger.info("SignalManager started")

    async def stop(self) -> None:
        """Stop the signal manager."""
        self._running = False
        # Cancel all timer tasks
        for task in self._timer_tasks.values():
            task.cancel()
        self._timer_tasks.clear()
        logger.info("SignalManager stopped")

    async def send_signal(
        self,
        signal_type: SignalType,
        study_id: str,
        data: dict[str, Any] | None = None,
        source: str = "external",
    ) -> Signal:
        """Send a signal to a study."""
        signal = Signal(
            signal_id=str(uuid4()),
            signal_type=signal_type,
            study_id=study_id,
            timestamp=time.time(),
            data=data or {},
            source=source,
        )

        # Record event
        if self._event_store:
            from .event_store import EventType
            self._event_store.append(
                EventType.SIGNAL_RECEIVED,
                study_id,
                data=signal.to_dict(),
            )

        # Queue for processing
        await self._signal_queue.put(signal)
        self._pending_signals[signal.signal_id] = signal

        logger.info("Signal sent: %s to study %s", signal_type.value, study_id)
        return signal

    async def _process_signals(self) -> None:
        """Process signals from the queue."""
        while self._running:
            try:
                signal = await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            handlers = self._signal_registry.get_handlers(signal.signal_type)
            for handler in handlers:
                try:
                    await handler.handle_signal(signal)
                except Exception as exc:
                    logger.error("Signal handler error: %s", exc)

            self._pending_signals.pop(signal.signal_id, None)

    def create_timer(
        self,
        study_id: str,
        delay_seconds: float,
        callback_name: str,
        data: dict[str, Any] | None = None,
    ) -> Timer:
        """Create a timer."""
        timer = Timer(
            timer_id=str(uuid4()),
            study_id=study_id,
            delay_seconds=delay_seconds,
            callback_name=callback_name,
            data=data or {},
        )

        self._pending_timers[timer.timer_id] = timer

        # Schedule the timer
        task = asyncio.create_task(self._fire_timer(timer))
        self._timer_tasks[timer.timer_id] = task

        logger.info(
            "Timer created: %s for study %s, fires in %.1fs",
            callback_name, study_id, delay_seconds,
        )
        return timer

    async def _fire_timer(self, timer: Timer) -> None:
        """Fire a timer after its delay."""
        try:
            await asyncio.sleep(timer.delay_seconds)

            timer.fired_at = time.time()
            timer.status = TimerStatus.FIRED

            # Record event
            if self._event_store:
                from .event_store import EventType
                self._event_store.append(
                    EventType.TIMER_FIRED,
                    timer.study_id,
                    data=timer.to_dict(),
                )

            # Execute callback
            callback = self._timer_registry.get_callback(timer.callback_name)
            if callback:
                try:
                    await callback.on_timer_fired(timer)
                except Exception as exc:
                    logger.error("Timer callback error: %s", exc)

            self._pending_timers.pop(timer.timer_id, None)
            self._fired_timers[timer.timer_id] = timer

        except asyncio.CancelledError:
            timer.status = TimerStatus.CANCELLED
            self._pending_timers.pop(timer.timer_id, None)

    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel a pending timer."""
        task = self._timer_tasks.get(timer_id)
        if task and not task.done():
            task.cancel()
            timer = self._pending_timers.get(timer_id)
            if timer:
                timer.status = TimerStatus.CANCELLED
            return True
        return False

    def get_pending_timers(self, study_id: str | None = None) -> list[Timer]:
        """Get pending timers, optionally filtered by study."""
        timers = list(self._pending_timers.values())
        if study_id:
            timers = [t for t in timers if t.study_id == study_id]
        return timers

    def get_fired_timers(self, study_id: str | None = None) -> list[Timer]:
        """Get fired timers, optionally filtered by study."""
        timers = list(self._fired_timers.values())
        if study_id:
            timers = [t for t in timers if t.study_id == study_id]
        return timers


# Default signal handler implementations

class PauseHandler(SignalHandler):
    """Handles pause signals."""

    def __init__(self, scheduler: Any | None = None):
        self._scheduler = scheduler

    async def handle_signal(self, signal: Signal) -> None:
        if self._scheduler:
            control = self._scheduler.get_control_token(signal.study_id)
            if control:
                control.paused = True
                logger.info("Study %s paused", signal.study_id)


class ResumeHandler(SignalHandler):
    """Handles resume signals."""

    def __init__(self, scheduler: Any | None = None):
        self._scheduler = scheduler

    async def handle_signal(self, signal: Signal) -> None:
        if self._scheduler:
            control = self._scheduler.get_control_token(signal.study_id)
            if control:
                control.paused = False
                logger.info("Study %s resumed", signal.study_id)


class CancelHandler(SignalHandler):
    """Handles cancel signals."""

    def __init__(self, scheduler: Any | None = None):
        self._scheduler = scheduler

    async def handle_signal(self, signal: Signal) -> None:
        if self._scheduler:
            control = self._scheduler.get_control_token(signal.study_id)
            if control:
                control.cancelled = True
                logger.info("Study %s cancelled", signal.study_id)


class DirectiveHandler(SignalHandler):
    """Handles directive injection signals."""

    def __init__(self, study_store: Any | None = None):
        self._study_store = study_store

    async def handle_signal(self, signal: Signal) -> None:
        if self._study_store:
            directive_text = signal.data.get("directive_text")
            if directive_text:
                self._study_store.add_directive(
                    signal.study_id,
                    directive_text,
                    source=signal.source,
                )
                logger.info("Directive injected to study %s", signal.study_id)


# Default timer callback implementations

class MonitorCheckCallback(TimerCallback):
    """Periodic monitoring check callback."""

    def __init__(self, runner: Any | None = None):
        self._runner = runner

    async def on_timer_fired(self, timer: Timer) -> None:
        if self._runner:
            try:
                check = await asyncio.to_thread(self._runner._run_monitor_check)
                logger.info("Monitor check completed: %s", check)
            except Exception as exc:
                logger.error("Monitor check failed: %s", exc)


# Default registries
_default_signal_registry = SignalRegistry()
_default_signal_registry.register(SignalType.PAUSE, PauseHandler())
_default_signal_registry.register(SignalType.RESUME, ResumeHandler())
_default_signal_registry.register(SignalType.CANCEL, CancelHandler())
_default_signal_registry.register(SignalType.INJECT_DIRECTIVE, DirectiveHandler())

_default_timer_registry = TimerRegistry()
_default_timer_registry.register("monitor_check", MonitorCheckCallback())


def get_signal_manager(
    event_store: Any | None = None,
    scheduler: Any | None = None,
    study_store: Any | None = None,
) -> SignalManager:
    """Create a signal manager with default handlers."""
    registry = SignalRegistry()
    registry.register(SignalType.PAUSE, PauseHandler(scheduler))
    registry.register(SignalType.RESUME, ResumeHandler(scheduler))
    registry.register(SignalType.CANCEL, CancelHandler(scheduler))
    registry.register(SignalType.INJECT_DIRECTIVE, DirectiveHandler(study_store))

    return SignalManager(
        event_store=event_store,
        signal_registry=registry,
    )
