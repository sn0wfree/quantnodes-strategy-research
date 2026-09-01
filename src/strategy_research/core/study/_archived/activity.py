"""Activity Isolation — Separating deterministic Workflow from non-deterministic Activities.

Inspired by Temporal's architecture:
- Workflow: Deterministic, replayable logic (no side effects)
- Activity: Non-deterministic operations (LLM calls, file I/O, API calls)

This separation enables:
- Safe replay from Event History
- Crash recovery without duplicate side effects
- Testing workflow logic without real LLM calls

Design: workflow.py defines the deterministic workflow, activity.py defines
the non-deterministic activities.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)


class ActivityStatus(str, Enum):
    """Activity execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ActivityResult:
    """Result of an activity execution."""
    activity_id: str
    activity_name: str
    status: ActivityStatus
    result: Any = None
    error: str | None = None
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "activity_name": self.activity_name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": self.duration_s,
        }


class Activity(Protocol):
    """Protocol for non-deterministic activities."""

    @property
    def name(self) -> str:
        """Activity name for logging and replay."""
        ...

    async def execute(self, **kwargs: Any) -> Any:
        """Execute the activity (non-deterministic)."""
        ...

    def serialize_args(self, **kwargs: Any) -> dict[str, Any]:
        """Serialize arguments for event recording."""
        ...

    def deserialize_result(self, data: Any) -> Any:
        """Deserialize result from event."""
        ...


class BaseActivity:
    """Base class for activities with common functionality."""

    def __init__(self, name: str | None = None):
        self._name = name or self.__class__.__name__

    @property
    def name(self) -> str:
        return self._name

    def serialize_args(self, **kwargs: Any) -> dict[str, Any]:
        """Default serialization: convert to JSON-safe dict."""
        import json
        return json.loads(json.dumps(kwargs, default=str))

    def deserialize_result(self, data: Any) -> Any:
        """Default deserialization: return as-is."""
        return data


class AgentActivity(BaseActivity):
    """Activity for spawning LLM agents (non-deterministic)."""

    def __init__(self):
        super().__init__("spawn_agent")

    async def execute(
        self,
        agent_name: str,
        workspace_path: str,
        strategy_name: str,
        current_state: dict,
        previous_outputs: list,
        **kwargs: Any,
    ) -> str:
        """Spawn an agent via LLM (non-deterministic)."""
        from strategy_research.core.autoresearch import spawn_agent
        return spawn_agent(
            agent_name=agent_name,
            workspace_path=workspace_path,
            strategy_name=strategy_name,
            current_state=current_state,
            previous_outputs=previous_outputs,
            **kwargs,
        )


class BacktestActivity(BaseActivity):
    """Activity for running backtests (non-deterministic)."""

    def __init__(self):
        super().__init__("run_backtest")

    async def execute(
        self,
        workspace_path: str,
        strategy_path: str,
        **kwargs: Any,
    ) -> dict:
        """Run a backtest (non-deterministic)."""
        from strategy_research.core.backtest import run_backtest_script
        return run_backtest_script(
            workspace_path=workspace_path,
            strategy_path=strategy_path,
            **kwargs,
        )


class FileWriteActivity(BaseActivity):
    """Activity for writing files (non-deterministic)."""

    def __init__(self):
        super().__init__("write_file")

    async def execute(
        self,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> bool:
        """Write content to file."""
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return True


class ActivityRegistry:
    """Registry for all available activities."""

    def __init__(self):
        self._activities: dict[str, Activity] = {}

    def register(self, activity: Activity) -> None:
        """Register an activity."""
        self._activities[activity.name] = activity

    def get(self, name: str) -> Activity | None:
        """Get an activity by name."""
        return self._activities.get(name)

    def list_activities(self) -> list[str]:
        """List all registered activity names."""
        return list(self._activities.keys())


# Default registry with built-in activities
_default_registry = ActivityRegistry()
_default_registry.register(AgentActivity())
_default_registry.register(BacktestActivity())
_default_registry.register(FileWriteActivity())


def get_activity_registry() -> ActivityRegistry:
    """Get the default activity registry."""
    return _default_registry


class ActivityExecutor:
    """Executor that runs activities with event recording."""

    def __init__(
        self,
        registry: ActivityRegistry | None = None,
        event_store: Any | None = None,
    ):
        self._registry = registry or get_activity_registry()
        self._event_store = event_store
        self._results: dict[str, ActivityResult] = {}

    async def execute_activity(
        self,
        activity_name: str,
        study_id: str,
        **kwargs: Any,
    ) -> ActivityResult:
        """Execute an activity with event recording."""
        activity = self._registry.get(activity_name)
        if activity is None:
            return ActivityResult(
                activity_id=str(uuid4()),
                activity_name=activity_name,
                status=ActivityStatus.FAILED,
                error=f"Activity not found: {activity_name}",
            )

        activity_id = str(uuid4())
        started_at = time.time()

        # Record activity started
        if self._event_store:
            from .event_store import EventType
            self._event_store.append(
                EventType.AGENT_SPAWNED if activity_name == "spawn_agent" else EventType.BACKTEST_STARTED,
                study_id,
                data={
                    "activity_id": activity_id,
                    "activity_name": activity_name,
                    "args": activity.serialize_args(**kwargs),
                },
            )

        try:
            # Execute the activity
            result = await activity.execute(**kwargs)
            completed_at = time.time()

            activity_result = ActivityResult(
                activity_id=activity_id,
                activity_name=activity_name,
                status=ActivityStatus.COMPLETED,
                result=result,
                started_at=started_at,
                completed_at=completed_at,
                duration_s=completed_at - started_at,
            )

            # Record activity completed
            if self._event_store:
                from .event_store import EventType
                self._event_store.append(
                    EventType.AGENT_COMPLETED if activity_name == "spawn_agent" else EventType.BACKTEST_COMPLETED,
                    study_id,
                    data={
                        "activity_id": activity_id,
                        "activity_name": activity_name,
                        "duration_s": completed_at - started_at,
                    },
                )

            self._results[activity_id] = activity_result
            return activity_result

        except Exception as exc:
            completed_at = time.time()

            activity_result = ActivityResult(
                activity_id=activity_id,
                activity_name=activity_name,
                status=ActivityStatus.FAILED,
                error=str(exc),
                started_at=started_at,
                completed_at=completed_at,
                duration_s=completed_at - started_at,
            )

            # Record activity failed
            if self._event_store:
                from .event_store import EventType
                self._event_store.append(
                    EventType.AGENT_FAILED if activity_name == "spawn_agent" else EventType.BACKTEST_FAILED,
                    study_id,
                    data={
                        "activity_id": activity_id,
                        "activity_name": activity_name,
                        "error": str(exc),
                    },
                )

            self._results[activity_id] = activity_result
            return activity_result

    def get_result(self, activity_id: str) -> ActivityResult | None:
        """Get a cached activity result."""
        return self._results.get(activity_id)


class WorkflowEngine:
    """Deterministic workflow engine that records events and replays activities.

    The workflow itself is deterministic - it only records what activities
    should be executed. The actual execution happens in ActivityExecutor.
    On replay, recorded results are used instead of re-executing activities.
    """

    def __init__(
        self,
        event_store: Any | None = None,
        activity_executor: ActivityExecutor | None = None,
    ):
        self._event_store = event_store
        self._executor = activity_executor or ActivityExecutor(event_store=event_store)
        self._completed_activities: dict[str, ActivityResult] = {}

    async def run_activity(
        self,
        activity_name: str,
        study_id: str,
        activity_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run an activity, using cached result if available (replay-safe)."""
        # Check if we have a cached result (replay path)
        if activity_id and activity_id in self._completed_activities:
            cached = self._completed_activities[activity_id]
            if cached.status == ActivityStatus.COMPLETED:
                return cached.result
            elif cached.status == ActivityStatus.FAILED:
                raise RuntimeError(cached.error)

        # Execute the activity
        result = await self._executor.execute_activity(
            activity_name, study_id, **kwargs,
        )

        # Cache the result
        if result.activity_id:
            self._completed_activities[result.activity_id] = result

        if result.status == ActivityStatus.FAILED:
            raise RuntimeError(result.error)

        return result.result

    def register_completed_activity(self, result: ActivityResult) -> None:
        """Register a completed activity result (for replay)."""
        self._completed_activities[result.activity_id] = result

    def clear_cache(self) -> None:
        """Clear the activity cache."""
        self._completed_activities.clear()
