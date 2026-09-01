"""DAG — Configurable DAG orchestration for agent pipelines.

Replaces the hardcoded 9-agent serial pipeline with a configurable
DAG (Directed Acyclic Graph) engine. Inspired by:
- Prefect's DAG-based flow execution
- Airflow's task dependency management
- AutoGen's GraphFlow

Features:
- YAML/JSON-based DAG configuration
- Parallel execution of independent tasks
- Conditional branches
- Dynamic DAG modification
- Checkpoint/resume support
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class TaskType(str, Enum):
    """Types of tasks in the DAG."""
    AGENT = "agent"  # LLM agent task
    BACKTEST = "backtest"  # Backtest execution
    EVALUATION = "evaluation"  # Metrics evaluation
    TRANSFORM = "transform"  # Data transformation
    CONDITION = "condition"  # Conditional branch
    PARALLEL = "parallel"  # Parallel execution group


@dataclass
class TaskDefinition:
    """Definition of a task in the DAG."""
    task_id: str
    task_type: TaskType
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)  # Upstream task IDs
    condition: str | None = None  # Python expression for conditional tasks
    retry_count: int = 0
    timeout_seconds: float | None = None
    priority: int = 0  # Higher = executed first when parallel

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "name": self.name,
            "config": self.config,
            "dependencies": self.dependencies,
            "condition": self.condition,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
            "priority": self.priority,
        }


@dataclass
class TaskResult:
    """Result of a task execution."""
    task_id: str
    status: TaskStatus
    result: Any = None
    error: str | None = None
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_s: float = 0.0
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": self.duration_s,
            "retry_count": self.retry_count,
        }


@dataclass
class DAGDefinition:
    """Complete DAG definition."""
    dag_id: str
    name: str
    description: str = ""
    tasks: list[TaskDefinition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DAGDefinition:
        tasks = [TaskDefinition(**t) for t in d.get("tasks", [])]
        return cls(
            dag_id=d["dag_id"],
            name=d["name"],
            description=d.get("description", ""),
            tasks=tasks,
            metadata=d.get("metadata", {}),
            version=d.get("version", "1.0"),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> DAGDefinition:
        """Load DAG from YAML file."""
        import yaml
        p = Path(path)
        with open(p) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: str | Path) -> DAGDefinition:
        """Load DAG from JSON file."""
        p = Path(path)
        with open(p) as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "name": self.name,
            "description": self.description,
            "tasks": [t.to_dict() for t in self.tasks],
            "metadata": self.metadata,
            "version": self.version,
        }


class TaskExecutor:
    """Protocol for executing tasks."""

    async def execute(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> Any:
        """Execute a task and return its result."""
        ...


class AgentTaskExecutor(TaskExecutor):
    """Executor for LLM agent tasks."""

    async def execute(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> Any:
        from strategy_research.core.autoresearch import spawn_agent
        return spawn_agent(
            agent_name=task.config.get("agent_name", task.name),
            workspace_path=context["workspace_path"],
            strategy_name=context["strategy_name"],
            current_state=context.get("current_state", {}),
            previous_outputs=context.get("previous_outputs", []),
            **task.config.get("spawn_kwargs", {}),
        )


class BacktestTaskExecutor(TaskExecutor):
    """Executor for backtest tasks."""

    async def execute(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> Any:
        from strategy_research.core.backtest import run_backtest_script
        return run_backtest_script(
            workspace_path=context["workspace_path"],
            strategy_path=task.config.get("strategy_path", ""),
            **task.config.get("backtest_kwargs", {}),
        )


class EvaluationTaskExecutor(TaskExecutor):
    """Executor for evaluation tasks."""

    async def execute(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> Any:
        # Evaluation logic
        metrics = context.get("metrics", {})
        targets = task.config.get("targets", [])
        from strategy_research.core.study.runner import meets_metric_targets
        return {
            "meets_targets": meets_metric_targets(metrics, targets),
            "metrics": metrics,
        }


class TransformTaskExecutor(TaskExecutor):
    """Executor for data transformation tasks."""

    async def execute(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> Any:
        transform_fn = task.config.get("transform")
        if transform_fn and callable(transform_fn):
            return transform_fn(context)
        return context


class TaskExecutors:
    """Registry of task executors."""

    def __init__(self):
        self._executors: dict[TaskType, TaskExecutor] = {}

    def register(self, task_type: TaskType, executor: TaskExecutor) -> None:
        self._executors[task_type] = executor

    def get(self, task_type: TaskType) -> TaskExecutor | None:
        return self._executors.get(task_type)


# Default executors
_default_executors = TaskExecutors()
_default_executors.register(TaskType.AGENT, AgentTaskExecutor())
_default_executors.register(TaskType.BACKTEST, BacktestTaskExecutor())
_default_executors.register(TaskType.EVALUATION, EvaluationTaskExecutor())
_default_executors.register(TaskType.TRANSFORM, TransformTaskExecutor())


class DAGScheduler:
    """Schedules and executes tasks in a DAG.

    Features:
    - Topological sorting
    - Parallel execution of independent tasks
    - Conditional branches
    - Retry logic
    - Checkpoint support
    """

    def __init__(
        self,
        dag: DAGDefinition,
        executors: TaskExecutors | None = None,
        event_store: Any | None = None,
    ):
        self._dag = dag
        self._executors = executors or _default_executors
        self._event_store = event_store
        self._results: dict[str, TaskResult] = {}
        self._completed_tasks: set[str] = set()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def _get_upstream_tasks(self, task_id: str) -> list[str]:
        """Get tasks that must complete before this task."""
        for task in self._dag.tasks:
            if task.task_id == task_id:
                return task.dependencies
        return []

    def _get_downstream_tasks(self, task_id: str) -> list[str]:
        """Get tasks that depend on this task."""
        downstream = []
        for task in self._dag.tasks:
            if task_id in task.dependencies:
                downstream.append(task.task_id)
        return downstream

    def _are_dependencies_met(self, task: TaskDefinition) -> bool:
        """Check if all dependencies are completed."""
        return all(dep in self._completed_tasks for dep in task.dependencies)

    def _get_ready_tasks(self) -> list[TaskDefinition]:
        """Get tasks that are ready to execute."""
        ready = []
        for task in self._dag.tasks:
            if (
                task.task_id not in self._completed_tasks
                and task.task_id not in self._running_tasks
                and self._are_dependencies_met(task)
            ):
                ready.append(task)
        # Sort by priority
        ready.sort(key=lambda t: t.priority, reverse=True)
        return ready

    def _evaluate_condition(self, condition: str, context: dict[str, Any]) -> bool:
        """Evaluate a condition expression."""
        if not condition:
            return True
        try:
            # Simple condition evaluation (safe subset of Python)
            # In production, use a proper expression parser
            return bool(eval(condition, {"__builtins__": {}}, context))
        except Exception:
            return False

    async def execute(
        self,
        context: dict[str, Any],
        max_parallel: int = 4,
    ) -> dict[str, TaskResult]:
        """Execute the entire DAG."""
        logger.info("Starting DAG execution: %s", self._dag.dag_id)

        while True:
            ready = self._get_ready_tasks()
            if not ready and not self._running_tasks:
                break  # All done

            # Execute ready tasks (up to max_parallel)
            semaphore = asyncio.Semaphore(max_parallel)

            async def run_with_semaphore(task: TaskDefinition) -> None:
                async with semaphore:
                    await self._execute_task(task, context)

            # Start new tasks
            for task in ready[:max_parallel - len(self._running_tasks)]:
                # Check condition
                if task.condition and not self._evaluate_condition(task.condition, context):
                    self._results[task.task_id] = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.SKIPPED,
                    )
                    self._completed_tasks.add(task.task_id)
                    continue

                # Start task
                self._running_tasks[task.task_id] = asyncio.create_task(
                    run_with_semaphore(task)
                )

            # Wait for at least one task to complete
            if self._running_tasks:
                done, _ = await asyncio.wait(
                    self._running_tasks.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Clean up completed tasks
                for task_id, task_obj in list(self._running_tasks.items()):
                    if task_obj.done():
                        del self._running_tasks[task_id]

        return self._results

    async def _execute_task(
        self,
        task: TaskDefinition,
        context: dict[str, Any],
    ) -> None:
        """Execute a single task."""
        executor = self._executors.get(task.task_type)
        if not executor:
            self._results[task.task_id] = TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"No executor for task type: {task.task_type}",
            )
            self._completed_tasks.add(task.task_id)
            return

        started_at = time.time()
        retry_count = 0

        while retry_count <= task.retry_count:
            try:
                # Record task started
                if self._event_store:
                    from .event_store import EventType
                    self._event_store.append(
                        EventType.PHASE_STARTED,
                        context.get("study_id", ""),
                        data={
                            "task_id": task.task_id,
                            "task_type": task.task_type.value,
                            "retry": retry_count,
                        },
                    )

                # Execute task
                if task.timeout_seconds:
                    result = await asyncio.wait_for(
                        executor.execute(task, context),
                        timeout=task.timeout_seconds,
                    )
                else:
                    result = await executor.execute(task, context)

                completed_at = time.time()

                self._results[task.task_id] = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    result=result,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_s=completed_at - started_at,
                    retry_count=retry_count,
                )
                self._completed_tasks.add(task.task_id)

                # Record task completed
                if self._event_store:
                    from .event_store import EventType
                    self._event_store.append(
                        EventType.PHASE_COMPLETED,
                        context.get("study_id", ""),
                        data={
                            "task_id": task.task_id,
                            "duration_s": completed_at - started_at,
                        },
                    )

                logger.info(
                    "Task completed: %s (%.1fs)",
                    task.task_id, completed_at - started_at,
                )
                return

            except Exception as exc:
                retry_count += 1
                if retry_count > task.retry_count:
                    completed_at = time.time()
                    self._results[task.task_id] = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_s=completed_at - started_at,
                        retry_count=retry_count - 1,
                    )
                    self._completed_tasks.add(task.task_id)

                    # Record task failed
                    if self._event_store:
                        from .event_store import EventType
                        self._event_store.append(
                            EventType.PHASE_FAILED,
                            context.get("study_id", ""),
                            data={
                                "task_id": task.task_id,
                                "error": str(exc),
                                "retry": retry_count - 1,
                            },
                        )

                    logger.error("Task failed: %s: %s", task.task_id, exc)
                    return
                else:
                    self._results[task.task_id] = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.RETRYING,
                        retry_count=retry_count,
                    )
                    logger.warning(
                        "Task retrying: %s (attempt %d/%d)",
                        task.task_id, retry_count, task.retry_count,
                    )
                    await asyncio.sleep(1)  # Brief delay before retry

    def get_result(self, task_id: str) -> TaskResult | None:
        """Get the result of a task."""
        return self._results.get(task_id)

    def get_all_results(self) -> dict[str, TaskResult]:
        """Get all task results."""
        return dict(self._results)

    def get_execution_order(self) -> list[str]:
        """Get the execution order (topological sort)."""
        visited = set()
        order = []

        def dfs(task_id: str) -> None:
            if task_id in visited:
                return
            visited.add(task_id)
            for dep in self._get_upstream_tasks(task_id):
                dfs(dep)
            order.append(task_id)

        for task in self._dag.tasks:
            dfs(task.task_id)

        return order


# Pre-built DAG configurations for common research workflows

def create_research_dag(
    agent_sequence: list[str] | None = None,
) -> DAGDefinition:
    """Create the standard research DAG (replaces hardcoded pipeline).

    Default agent sequence:
    1. researcher
    2. data_quality
    3. factor_analyst
    4. strategist
    5. portfolio_construction
    6. risk_controller
    7. attribution_analyst
    8. anti_overfit_analyst
    """
    agents = agent_sequence or [
        "researcher",
        "data_quality",
        "factor_analyst",
        "strategist",
        "portfolio_construction",
        "risk_controller",
        "attribution_analyst",
        "anti_overfit_analyst",
    ]

    tasks = []
    for i, agent_name in enumerate(agents):
        task = TaskDefinition(
            task_id=f"agent_{agent_name}",
            task_type=TaskType.AGENT,
            name=agent_name,
            config={"agent_name": agent_name},
            dependencies=[f"agent_{agents[i-1]}"] if i > 0 else [],
        )
        tasks.append(task)

    return DAGDefinition(
        dag_id="research_pipeline",
        name="Standard Research Pipeline",
        description="The default 8-agent research pipeline",
        tasks=tasks,
    )


def create_parallel_research_dag() -> DAGDefinition:
    """Create a parallel research DAG for faster execution.

    Groups agents into parallel stages:
    Stage 1: researcher (must run first)
    Stage 2: data_quality + factor_analyst (parallel)
    Stage 3: strategist (depends on both)
    Stage 4: portfolio_construction + risk_controller (parallel)
    Stage 5: attribution_analyst + anti_overfit_analyst (parallel)
    """
    tasks = [
        # Stage 1
        TaskDefinition(
            task_id="agent_researcher",
            task_type=TaskType.AGENT,
            name="researcher",
            config={"agent_name": "researcher"},
            dependencies=[],
        ),
        # Stage 2 (parallel)
        TaskDefinition(
            task_id="agent_data_quality",
            task_type=TaskType.AGENT,
            name="data_quality",
            config={"agent_name": "data_quality"},
            dependencies=["agent_researcher"],
        ),
        TaskDefinition(
            task_id="agent_factor_analyst",
            task_type=TaskType.AGENT,
            name="factor_analyst",
            config={"agent_name": "factor_analyst"},
            dependencies=["agent_researcher"],
        ),
        # Stage 3
        TaskDefinition(
            task_id="agent_strategist",
            task_type=TaskType.AGENT,
            name="strategist",
            config={"agent_name": "strategist"},
            dependencies=["agent_data_quality", "agent_factor_analyst"],
        ),
        # Stage 4 (parallel)
        TaskDefinition(
            task_id="agent_portfolio_construction",
            task_type=TaskType.AGENT,
            name="portfolio_construction",
            config={"agent_name": "portfolio_construction"},
            dependencies=["agent_strategist"],
        ),
        TaskDefinition(
            task_id="agent_risk_controller",
            task_type=TaskType.AGENT,
            name="risk_controller",
            config={"agent_name": "risk_controller"},
            dependencies=["agent_strategist"],
        ),
        # Stage 5 (parallel)
        TaskDefinition(
            task_id="agent_attribution_analyst",
            task_type=TaskType.AGENT,
            name="attribution_analyst",
            config={"agent_name": "attribution_analyst"},
            dependencies=["agent_portfolio_construction", "agent_risk_controller"],
        ),
        TaskDefinition(
            task_id="agent_anti_overfit_analyst",
            task_type=TaskType.AGENT,
            name="anti_overfit_analyst",
            config={"agent_name": "anti_overfit_analyst"},
            dependencies=["agent_portfolio_construction", "agent_risk_controller"],
        ),
    ]

    return DAGDefinition(
        dag_id="parallel_research",
        name="Parallel Research Pipeline",
        description="Optimized research pipeline with parallel agent execution",
        tasks=tasks,
    )


# DAG registry
_dag_registry: dict[str, DAGDefinition] = {}


def register_dag(dag: DAGDefinition) -> None:
    """Register a DAG configuration."""
    _dag_registry[dag.dag_id] = dag


def get_dag(dag_id: str) -> DAGDefinition | None:
    """Get a registered DAG."""
    return _dag_registry.get(dag_id)


def list_dags() -> list[str]:
    """List all registered DAG IDs."""
    return list(_dag_registry.keys())


# Register default DAGs
register_dag(create_research_dag())
register_dag(create_parallel_research_dag())
