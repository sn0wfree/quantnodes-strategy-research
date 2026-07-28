"""WorkflowEventObserver — push workflow state changes to subscribers.

Defines:
  - WorkflowEventObserver: Protocol with ``on_event(event, data)``
  - WorkflowEventBus: Holds a list of observers; emits events safely
  - LoggerObserver: built-in observer that logs every event
  - GoalPanelObserver: built-in observer that pushes events to GoalPanel

Events emitted by GoalWorkflowRunner:
  - ``workflow_start``        — workflow begins executing
  - ``workflow_paused``       — user/auto pause
  - ``workflow_resumed``      — resume after pause
  - ``workflow_completed``    — successful completion
  - ``workflow_failed``       — fatal error
  - ``layer_start``           — new layer begins
  - ``layer_complete``        — layer finished
  - ``agent_start``           — agent begins running
  - ``agent_complete``        — agent finished successfully
  - ``agent_error``           — agent failed (retried or skipped)
  - ``evidence_collected``    — evidence appended to goal
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class WorkflowEventObserver(Protocol):
    """Receiver for GoalWorkflowRunner state-change events."""

    def on_event(self, event: str, data: dict[str, Any]) -> None:
        """Called by ``WorkflowEventBus.emit`` after each event."""
        ...


class WorkflowEventBus:
    """Manages observers and emits events safely.

    Observer failures are caught and logged — one broken observer must
    not break the workflow.
    """

    def __init__(self) -> None:
        self._observers: list[WorkflowEventObserver] = []

    def subscribe(self, observer: WorkflowEventObserver) -> None:
        """Add an observer."""
        self._observers.append(observer)

    def unsubscribe(self, observer: WorkflowEventObserver) -> None:
        """Remove an observer (no-op if absent)."""
        try:
            self._observers.remove(observer)
        except ValueError:
            pass

    def clear(self) -> None:
        """Remove all observers."""
        self._observers.clear()

    def emit(self, event: str, **data: Any) -> None:
        """Dispatch an event to all observers."""
        for obs in self._observers:
            try:
                obs.on_event(event, dict(data))
            except Exception as exc:                    # noqa: BLE001
                logger.warning(
                    "Observer %s failed for event %r: %s",
                    getattr(obs, "__class__", type(obs)).__name__,
                    event,
                    exc,
                )

    def __len__(self) -> int:
        return len(self._observers)


# ── Built-in Observers ────────────────────────────────────────


class LoggerObserver:
    """Logs every workflow event at INFO level."""

    def on_event(self, event: str, data: dict[str, Any]) -> None:
        logger.info("workflow event: %s %s", event, data)


class CollectingObserver:
    """Collects events in memory — handy for tests and debugging."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def on_event(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))

    def clear(self) -> None:
        self.events.clear()


class GoalPanelObserver:
    """Pushes workflow events to a GoalPanel widget.

    The panel must expose ``on_workflow_event(event, data)``.
    """

    def __init__(self, panel: Any) -> None:
        self._panel = panel

    def on_event(self, event: str, data: dict[str, Any]) -> None:
        try:
            self._panel.on_workflow_event(event, data)
        except AttributeError:
            logger.warning(
                "GoalPanel %s has no on_workflow_event method",
                type(self._panel).__name__,
            )
        except Exception as exc:                    # noqa: BLE001
            logger.warning("GoalPanel update failed: %s", exc)


__all__ = [
    "WorkflowEventObserver",
    "WorkflowEventBus",
    "LoggerObserver",
    "CollectingObserver",
    "GoalPanelObserver",
]