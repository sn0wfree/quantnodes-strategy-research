"""Goal Workflow Engine — DAG-based research workflow for goals.

P3.9 refactor: GoalWorkflowRunner now fully delegates to SwarmRuntime
+ GoalWorkflowHook.  The runner becomes a thin orchestrator that:
  1. Creates a goal in GoalStore
  2. Converts GoalWorkflowConfig → SwarmPreset
  3. Creates GoalWorkflowHook for evidence collection + auto-complete
  4. Delegates DAG execution to SwarmRuntime.execute()
  5. Syncs state back from the hook after execution
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Configuration Models ─────────────────────────────────────


@dataclass
class GoalWorkflowGoalConfig:
    """Goal-specific configuration for the workflow."""

    default_criteria: list[str] = field(default_factory=list)
    risk_tier: str = "research_general"


@dataclass
class GoalAgentConfig:
    """Configuration for a single agent node in the workflow."""

    id: str
    prompt_file: str
    tools: list[str] = field(default_factory=list)
    input_from: list[str] = field(default_factory=list)
    evidence_criterion: int = 0
    timeout: int = 120
    max_retries: int = 3
    condition: str | None = None


@dataclass
class CompletionConfig:
    """How the workflow completes the goal."""

    mode: str = "auto"  # auto | manual | lite
    auto_audit: bool = True
    require_all_evidence: bool = True


@dataclass
class BranchConfig:
    """Conditional branch configuration."""

    condition: str
    action: str  # skip | retry | redirect
    target: str
    reason: str = ""


@dataclass
class GoalWorkflowConfig:
    """Complete workflow configuration loaded from YAML."""

    name: str
    description: str
    version: str = "1.0"
    goal: GoalWorkflowGoalConfig = field(default_factory=GoalWorkflowGoalConfig)
    agents: list[GoalAgentConfig] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)
    completion: CompletionConfig = field(default_factory=CompletionConfig)
    branches: list[BranchConfig] = field(default_factory=list)

    def to_swarm_preset(self) -> Any:
        """Convert to a SwarmPreset for use with SwarmRuntime (P3.3)."""
        from ..workflow.types import AgentCall
        from ..swarm.runtime import SwarmPreset

        agent_calls = []
        for agent in self.agents:
            agent_calls.append(AgentCall(
                agent_name=agent.id,
                prompt=agent.prompt_file,
                context={
                    "tools": agent.tools,
                    "input_from": agent.input_from,
                    "evidence_criterion": agent.evidence_criterion,
                    "timeout": agent.timeout,
                    "max_retries": agent.max_retries,
                },
            ))

        return SwarmPreset(
            name=self.name,
            description=self.description,
            agents=agent_calls,
            dag=self.dag,
            goal={
                "default_criteria": self.goal.default_criteria,
                "risk_tier": self.goal.risk_tier,
            },
            completion={
                "mode": self.completion.mode,
                "auto_audit": self.completion.auto_audit,
                "require_all_evidence": self.completion.require_all_evidence,
            },
            branches=[
                {"condition": b.condition, "action": b.action,
                 "target": b.target, "reason": b.reason}
                for b in self.branches
            ],
            version=self.version,
        )


# ── Workflow State ───────────────────────────────────────────


@dataclass
class GoalWorkflowState:
    """Tracks the execution state of a running workflow."""

    status: str = "idle"  # idle | running | paused | completed | error | cancelled
    current_layer: int = 0
    paused: bool = False
    cancelled: bool = False  # P3.4: immediate cancel flag
    pause_layer: int = -1
    agent_statuses: dict[str, str] = field(default_factory=dict)
    agent_errors: dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0
    start_time: float = 0.0
    error_message: str = ""

    def set_agent_status(self, agent_id: str, status: str, error: str = "") -> None:
        """Update an agent's execution status."""
        self.agent_statuses[agent_id] = status
        if error:
            self.agent_errors[agent_id] = error

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of the state."""
        return {
            "status": self.status,
            "current_layer": self.current_layer,
            "paused": self.paused,
            "agent_statuses": dict(self.agent_statuses),
            "evidence_count": self.evidence_count,
            "error_message": self.error_message,
        }


# ── Evidence Collector (legacy, used by decorator chain) ─────


class GoalEvidenceCollector:
    """Collects agent outputs and appends them as goal evidence."""

    def __init__(self, store: Any, session_id: str, goal_id: str) -> None:
        self._store = store
        self._session_id = session_id
        self._goal_id = goal_id

    def collect(self, agent_id: str, result: dict[str, Any], criterion_idx: int) -> int:
        """Append agent output as evidence. Returns number added (0/1)."""
        from .models import EvidenceInput

        answer = result.get("answer", "")
        if not answer or len(answer.strip()) < 10:
            return 0

        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return 0

        criteria = snapshot.get("criteria", [])
        if criterion_idx < 0 or criterion_idx >= len(criteria):
            return 0

        criterion_id = criteria[criterion_idx].get("criterion_id")
        if not criterion_id:
            return 0

        try:
            self._store.append_evidence(
                session_id=self._session_id,
                goal_id=self._goal_id,
                expected_goal_id=self._goal_id,
                evidence=EvidenceInput(
                    criterion_id=criterion_id,
                    text=answer[:2000],
                    source_provider="workflow",
                    source_type=agent_id,
                ),
            )
            return 1
        except Exception as exc:
            logger.warning("Failed to collect evidence from %s: %s", agent_id, exc)
            return 0


# ── Workflow Runner (P3.9: delegates to SwarmRuntime) ────────


class GoalWorkflowRunner:
    """DAG-based workflow executor for research goals.

    P3.9: Fully delegates to SwarmRuntime + GoalWorkflowHook.
    The runner is a thin orchestrator that:
      1. Creates a goal in GoalStore
      2. Converts GoalWorkflowConfig → SwarmPreset
      3. Creates GoalWorkflowHook for evidence collection
      4. Delegates DAG execution to SwarmRuntime.execute()
      5. Syncs state back after execution
    """

    def __init__(
        self,
        config: GoalWorkflowConfig,
        session_id: str,
        *,
        agent_runner: Any = None,
        agent_runner_type: str = "stub",
        store: Any = None,
        runner_kwargs: dict[str, Any] | None = None,
        use_validators: bool = True,
        workspace: Path | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            config: Workflow configuration loaded from YAML.
            session_id: Current session id.
            agent_runner: Pre-built AgentRunner (unused in P3.9, kept for compat).
            agent_runner_type: Type for SwarmRuntime controller selection.
            store: GoalStore instance.
            runner_kwargs: Pass-through kwargs for controller.
            use_validators: Whether to register validators (unused in P3.9).
            workspace: Workspace path for prompt file resolution.
        """
        self._config = config
        self._session_id = session_id
        self._store = store
        self._workspace = workspace or Path.cwd()
        self._event_bus = WorkflowEventBus()
        self._agent_runner_type = agent_runner_type
        self._runner_kwargs = runner_kwargs or {}

        self._state = GoalWorkflowState()
        self._goal_id: str = ""
        self._hook: Any = None  # GoalWorkflowHook after start()

    # ── Public API ────────────────────────────────────────────

    @property
    def state(self) -> GoalWorkflowState:
        return self._state

    @property
    def goal_id(self) -> str:
        return self._goal_id

    @property
    def event_bus(self) -> "WorkflowEventBus":
        return self._event_bus

    def subscribe(self, observer: Any) -> None:
        self._event_bus.subscribe(observer)

    def unsubscribe(self, observer: Any) -> None:
        self._event_bus.unsubscribe(observer)

    def get_progress(self) -> dict[str, Any]:
        """Return current workflow progress for UI display."""
        total_agents = len(self._config.agents)
        hook = self._hook
        evidence_count = hook.evidence_count if hook else 0
        completed = hook.completed if hook else False

        agent_statuses = dict(self._state.agent_statuses)
        completed_count = sum(
            1 for s in agent_statuses.values() if s in ("success", "skipped")
        )

        return {
            "goal_id": self._goal_id,
            "status": self._state.status,
            "current_layer": self._state.current_layer,
            "total_layers": len(self._config.agents),  # approximate
            "agents_completed": completed_count,
            "agents_total": total_agents,
            "evidence_count": evidence_count,
            "paused": self._state.paused,
            "agent_statuses": agent_statuses,
            "hook_completed": completed,
        }

    # ── Lifecycle (P3.9: delegates to SwarmRuntime) ───────────

    async def start(self, objective: str) -> str:
        """Start the workflow: create goal → delegate to SwarmRuntime → return goal_id."""
        if self._store is None:
            from .store import GoalStore
            self._store = GoalStore()

        from .context import default_goal_criteria
        from .models import RiskTier

        self._state.status = "running"
        self._state.start_time = time.time()
        self._event_bus.emit("workflow_start", workflow=self._config.name)

        # 1. Create the goal
        criteria = self._config.goal.default_criteria or default_goal_criteria()
        risk_tier = RiskTier(self._config.goal.risk_tier)

        goal = self._store.replace_goal(
            session_id=self._session_id,
            objective=objective,
            criteria=criteria,
            source="workflow",
            protocol=self._config.name,
            risk_tier=risk_tier,
        )
        self._goal_id = goal.goal_id

        # 2. Build evidence_map from agent configs
        evidence_map: dict[str, int] = {
            agent.id: agent.evidence_criterion
            for agent in self._config.agents
        }

        # 3. Create GoalWorkflowHook
        from .workflow_hook import GoalWorkflowHook
        from .completion_strategy import CompletionStrategyFactory

        self._hook = GoalWorkflowHook(
            session_id=self._session_id,
            goal_id=self._goal_id,
            evidence_map=evidence_map,
            store=self._store,
            completion_strategy=CompletionStrategyFactory.get(
                self._config.completion.mode,
            ),
            completion_mode=self._config.completion.mode,
            workflow_name=self._config.name,
            event_bus=self._event_bus,
        )

        # 4. Convert config → SwarmPreset
        preset = self._config.to_swarm_preset()

        # 5. Build SwarmRuntime
        from ..swarm.runtime import SwarmRuntime
        controller = self._build_controller()
        runtime = SwarmRuntime(controller=controller)

        # 6. Execute via SwarmRuntime (in thread since it's sync)
        try:
            result = await asyncio.to_thread(
                runtime.execute,
                preset,
                self._workspace,
                objective,
                [self._hook],
            )

            # 7. Sync state back from hook
            self._state.evidence_count = self._hook.evidence_count
            if self._hook.completed:
                self._state.status = "completed"
            elif result.success:
                self._state.status = "completed"
            else:
                self._state.status = "error"
                self._state.error_message = "One or more agents failed"

            logger.info(
                "Goal workflow finished: goal_id=%s status=%s evidence=%d",
                self._goal_id, self._state.status, self._hook.evidence_count,
            )

        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Goal workflow failed: %s", exc)

        return self._goal_id

    async def start_sub_workflow(
        self,
        objective: str,
        parent_goal_id: str,
    ) -> str:
        """Start a child workflow linked to a parent goal (P3.5).

        Creates a new goal with ``parent_goal_id`` set, then executes
        the DAG.  The child goal is independent — it has its own
        criteria, evidence, and completion lifecycle.

        Args:
            objective: Research objective for the child goal.
            parent_goal_id: The parent goal to link to.

        Returns:
            The child goal id.
        """
        if self._store is None:
            from .store import GoalStore
            self._store = GoalStore()

        from .context import default_goal_criteria
        from .models import RiskTier

        self._state.status = "running"
        self._state.start_time = time.time()
        self._event_bus.emit(
            "sub_workflow_start",
            parent_goal_id=parent_goal_id,
            workflow=self._config.name,
        )

        criteria = self._config.goal.default_criteria or default_goal_criteria()
        risk_tier = RiskTier(self._config.goal.risk_tier)

        goal = self._store.replace_goal(
            session_id=self._session_id,
            objective=objective,
            criteria=criteria,
            source="workflow",
            protocol=self._config.name,
            risk_tier=risk_tier,
            parent_goal_id=parent_goal_id,
        )
        self._goal_id = goal.goal_id

        evidence_map = {a.id: a.evidence_criterion for a in self._config.agents}

        from .workflow_hook import GoalWorkflowHook
        from .completion_strategy import CompletionStrategyFactory

        self._hook = GoalWorkflowHook(
            session_id=self._session_id,
            goal_id=self._goal_id,
            evidence_map=evidence_map,
            store=self._store,
            completion_strategy=CompletionStrategyFactory.get(
                self._config.completion.mode,
            ),
            completion_mode=self._config.completion.mode,
            workflow_name=self._config.name,
            event_bus=self._event_bus,
        )

        preset = self._config.to_swarm_preset()
        from ..swarm.runtime import SwarmRuntime
        controller = self._build_controller()
        runtime = SwarmRuntime(controller=controller)

        try:
            result = await asyncio.to_thread(
                runtime.execute,
                preset,
                self._workspace,
                objective,
                [self._hook],
            )
            self._state.evidence_count = self._hook.evidence_count
            self._state.status = "completed" if (
                self._hook.completed or result.success
            ) else "error"

            self._event_bus.emit(
                "sub_workflow_complete",
                parent_goal_id=parent_goal_id,
                child_goal_id=self._goal_id,
            )
            logger.info(
                "Sub-workflow finished: parent=%s child=%s status=%s",
                parent_goal_id, self._goal_id, self._state.status,
            )
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Sub-workflow failed: %s", exc)

        return self._goal_id

    def _build_controller(self) -> Any:
        """Build a WorkflowController for SwarmRuntime."""
        try:
            from ..workflow.controller import ControllerConfig, WorkflowController
            from ..workflow.agents import AgentRegistry
            cfg = ControllerConfig(timeout_seconds=120.0)
            return WorkflowController(
                registry=AgentRegistry(), adj={}, config=cfg,
            )
        except Exception as exc:
            logger.warning("Cannot build WorkflowController: %s", exc)
            return None

    def pause(self, *, immediate: bool = False) -> None:
        """Pause workflow execution.

        Args:
            immediate: If True, set cancelled flag (SwarmRuntime will
                       stop after current layer via should_stop).
        """
        self._state.paused = True
        if immediate:
            self._state.cancelled = True
            self._event_bus.emit(
                "workflow_cancelled", layer=self._state.current_layer,
            )
            logger.info("Goal workflow cancelled immediately")
        else:
            self._event_bus.emit(
                "workflow_paused", layer=self._state.current_layer,
            )
            logger.info("Goal workflow paused gracefully")

    def resume(self) -> None:
        """Resume workflow execution after pause."""
        self._state.paused = False
        self._state.cancelled = False
        self._event_bus.emit("workflow_resumed")
        logger.info("Goal workflow resumed")

    # ── Checkpoint (P3.6) ─────────────────────────────────────

    def checkpoint(self) -> Path | None:
        """Save current workflow state to disk for crash recovery.

        Returns the checkpoint directory path, or None if no store.
        """
        from .checkpoint_store import CheckpointStore as _CPS
        cp = _CPS()
        return cp.save(
            session_id=self._session_id,
            goal_id=self._goal_id,
            state=self._state.get_summary(),
            layer_results={},  # layer_results are in the hook now
            workflow_name=self._config.name,
        )

    @classmethod
    def resume_from_checkpoint(
        cls,
        session_id: str,
        goal_id: str,
        config: GoalWorkflowConfig,
        store: Any = None,
        workspace: Path | None = None,
    ) -> "GoalWorkflowRunner | None":
        """Restore a runner from a saved checkpoint (P3.6).

        Args:
            session_id: Session id from the checkpoint.
            goal_id: Goal id from the checkpoint.
            config: Workflow config (must match the original).
            store: GoalStore instance.
            workspace: Workspace path.

        Returns:
            A GoalWorkflowRunner with restored state, or None if
            checkpoint not found.
        """
        from .checkpoint_store import CheckpointStore as _CPS
        cp = _CPS()
        data = cp.load(session_id, goal_id)
        if data is None:
            return None

        runner = cls(
            config=config,
            session_id=session_id,
            store=store,
            workspace=workspace,
        )
        # Restore state
        state_data = data["state"]
        runner._state.status = state_data.get("status", "idle")
        runner._state.current_layer = state_data.get("current_layer", 0)
        runner._state.evidence_count = state_data.get("evidence_count", 0)
        runner._state.agent_statuses = state_data.get("agent_statuses", {})
        runner._goal_id = goal_id

        logger.info(
            "Resumed workflow from checkpoint: goal_id=%s layer=%d evidence=%d",
            goal_id, runner._state.current_layer, runner._state.evidence_count,
        )
        return runner


# Re-exports for convenience
from .event_bus import WorkflowEventBus  # noqa: E402

__all__ = [
    "GoalWorkflowConfig",
    "GoalWorkflowGoalConfig",
    "GoalAgentConfig",
    "CompletionConfig",
    "BranchConfig",
    "GoalWorkflowState",
    "GoalEvidenceCollector",
    "GoalWorkflowRunner",
    "WorkflowEventBus",
]