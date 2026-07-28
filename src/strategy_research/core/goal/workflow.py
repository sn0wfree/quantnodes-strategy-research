"""Goal Workflow Engine — DAG-based research workflow for goals.

Refactored (Phase 2 R1-R12) to:
  - Reuse PromptBuilder from core.workflow.prompt (R1+R2)
  - Use AgentRunnerFactory for pluggable runners (R3)
  - Use CompletionStrategyFactory for completion modes (R6)
  - Use ValidatorRegistry for the 9 default validators (R9)
  - Use WorkflowEventBus for state-change notifications (R8)
  - Apply with_retry/timeout/validation/evidence decorators (R7)
  - Inject GoalStore instead of repeated instantiation (R5)
  - Extract _run_layers() to deduplicate _execute_dag and continue_after_pause (R4)
  - Cache DAG layers to avoid repeated inversion (R11)
  - Delegate template logic to workflow YAML (R12)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
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


# ── Workflow State ───────────────────────────────────────────


@dataclass
class GoalWorkflowState:
    """Tracks the execution state of a running workflow."""

    status: str = "idle"  # idle | running | paused | completed | error | cancelled
    current_layer: int = 0
    paused: bool = False
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


# ── Evidence Collector ───────────────────────────────────────


class GoalEvidenceCollector:
    """Collects agent outputs and appends them as goal evidence."""

    def __init__(
        self,
        store: Any,
        session_id: str,
        goal_id: str,
    ) -> None:
        self._store = store
        self._session_id = session_id
        self._goal_id = goal_id

    def collect(
        self,
        agent_id: str,
        result: dict[str, Any],
        criterion_idx: int,
    ) -> int:
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

        text = answer[:2000]

        try:
            self._store.append_evidence(
                session_id=self._session_id,
                goal_id=self._goal_id,
                expected_goal_id=self._goal_id,
                evidence=EvidenceInput(
                    criterion_id=criterion_id,
                    text=text,
                    source_provider="workflow",
                    source_type=agent_id,
                ),
            )
            return 1
        except Exception as exc:
            logger.warning("Failed to collect evidence from %s: %s", agent_id, exc)
            return 0


# ── Workflow Runner ──────────────────────────────────────────


class GoalWorkflowRunner:
    """DAG-based workflow executor for research goals.

    Integrates:
      - AgentRunnerFactory for agent execution (R3)
      - ValidatorRegistry for output validation (R9)
      - CompletionStrategyFactory for completion modes (R6)
      - WorkflowEventBus for state-change notifications (R8)
      - Decorators for retry/timeout/validation/evidence (R7)
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
    ) -> None:
        """Initialize the runner.

        Args:
            config: Workflow configuration loaded from YAML.
            session_id: Current session id.
            agent_runner: Pre-built AgentRunner instance (overrides type).
            agent_runner_type: Type name for AgentRunnerFactory.create().
            store: GoalStore instance (default: new instance).
            runner_kwargs: Pass-through kwargs for AgentRunnerFactory.
            use_validators: Whether to apply the 9 default validators.
        """
        self._config = config
        self._session_id = session_id
        self._store = store  # type: ignore[assignment]
        self._event_bus = WorkflowEventBus()

        # R3: AgentRunner via factory
        if agent_runner is not None:
            self._agent_runner = agent_runner
        else:
            from ..workflow.agent_runner import AgentRunnerFactory
            kwargs = runner_kwargs or {}
            self._agent_runner = AgentRunnerFactory.create(
                agent_runner_type, **kwargs,
            )

        # R9: validators registry (lazy import to avoid circular)
        self._validators: dict[str, Any] = {}
        if use_validators:
            from .validator_registry import ValidatorRegistry
            for agent_cfg in config.agents:
                v = ValidatorRegistry.get(agent_cfg.id)
                if v is not None:
                    self._validators[agent_cfg.id] = v

        # R1+R2: PromptBuilder reuse
        from ..workflow.prompt import PromptBuilder
        self._prompt_builder = PromptBuilder()

        # R11: cache DAG layers
        self._layers_cache: list[list[str]] | None = None

        self._state = GoalWorkflowState()
        self._goal_id: str = ""
        self._evidence_collector: GoalEvidenceCollector | None = None
        self._layer_results: dict[str, dict[str, Any]] = {}

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
        """Add an observer for state-change events."""
        self._event_bus.subscribe(observer)

    def unsubscribe(self, observer: Any) -> None:
        self._event_bus.unsubscribe(observer)

    def get_progress(self) -> dict[str, Any]:
        """Return current workflow progress for UI display."""
        total_agents = len(self._config.agents)
        completed = sum(
            1 for s in self._state.agent_statuses.values()
            if s in ("success", "skipped")
        )
        running = sum(
            1 for s in self._state.agent_statuses.values()
            if s == "running"
        )
        total_layers = len(self._get_layers()) if self._config.dag else 0

        return {
            "goal_id": self._goal_id,
            "status": self._state.status,
            "current_layer": self._state.current_layer,
            "total_layers": total_layers,
            "agents_completed": completed,
            "agents_running": running,
            "agents_total": total_agents,
            "evidence_count": self._state.evidence_count,
            "paused": self._state.paused,
            "agent_statuses": dict(self._state.agent_statuses),
        }

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self, objective: str) -> str:
        """Start the workflow: create goal → execute DAG → return goal_id."""
        if self._store is None:
            from .store import GoalStore
            self._store = GoalStore()

        from .context import default_goal_criteria
        from .models import RiskTier

        self._state.status = "running"
        self._state.start_time = time.time()
        self._event_bus.emit("workflow_start", workflow=self._config.name)

        # Create the goal (R5: use injected store)
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
        self._evidence_collector = GoalEvidenceCollector(
            self._store, self._session_id, self._goal_id,
        )

        logger.info(
            "Goal workflow started: goal_id=%s workflow=%s",
            self._goal_id, self._config.name,
        )

        try:
            await self._execute_dag()
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Goal workflow failed: %s", exc)

        return self._goal_id

    # ── DAG execution (R4: deduplicated) ─────────────────────

    async def _execute_dag(self) -> None:
        """Execute the DAG layer by layer (starts from layer 0)."""
        await self._run_layers(0)

    async def continue_after_pause(self) -> None:
        """Resume execution from the paused layer."""
        if not self._state.paused:
            return
        self._state.paused = False
        self._state.status = "running"
        self._event_bus.emit("workflow_resumed")
        await self._run_layers(self._state.pause_layer)

    async def _run_layers(self, start_idx: int) -> None:
        """Common layer execution logic shared by start() and continue_after_pause()."""
        layers = self._get_layers()

        for layer_idx in range(start_idx, len(layers)):
            if self._state.paused:
                self._state.pause_layer = layer_idx
                self._state.status = "paused"
                self._event_bus.emit("workflow_paused", layer=layer_idx)
                return

            self._state.current_layer = layer_idx
            self._event_bus.emit("layer_start", layer=layer_idx)

            layer = self._apply_branches(layers[layer_idx])
            await self._execute_layer(layer, layer_idx)

            self._event_bus.emit("layer_complete", layer=layer_idx)

            if self._config.completion.mode == "auto":
                if self._check_all_criteria_covered():
                    await self._auto_complete()
                    return

        # All layers done
        if self._config.completion.mode == "auto":
            await self._auto_complete()
        else:
            self._state.status = "completed"
            self._event_bus.emit("workflow_completed")

    async def _execute_layer(self, layer: list[str], layer_idx: int) -> None:
        """Execute all agents in a layer in parallel."""
        if not layer:
            return
        tasks = [self._execute_agent(agent_id, layer_idx) for agent_id in layer]
        await asyncio.gather(*tasks)

    async def _execute_agent(self, agent_id: str, layer_idx: int) -> None:
        """Execute one agent using decorators (R7).

        Decorator chain (outer → inner):
          1. with_retry   — retry on exception
          2. with_timeout — wall-clock budget
          3. with_validation — validate output, retry on failure
          4. with_evidence_collection — auto-collect evidence
        """
        agent_config = self._get_agent_config(agent_id)
        if agent_config is None:
            self._state.set_agent_status(agent_id, "skipped", "not in config")
            return

        self._state.set_agent_status(agent_id, "running")
        self._event_bus.emit("agent_start", agent_id=agent_id, layer=layer_idx)

        # Build decorated pipeline
        from ..workflow.decorators import (
            with_evidence_collection,
            with_retry,
            with_timeout,
            with_validation,
        )

        run_func = self._agent_runner.run
        run_func = with_retry(agent_config.max_retries)(run_func)
        run_func = with_timeout(agent_config.timeout)(run_func)

        validator = self._validators.get(agent_id)
        if validator is not None:
            run_func = with_validation(validator)(run_func)

        if self._evidence_collector is not None:
            run_func = with_evidence_collection(
                self._evidence_collector,
                agent_config.evidence_criterion,
            )(run_func)

        try:
            prompt = self._build_prompt(agent_id)
            result = await run_func(
                agent_id,
                prompt,
                agent_config.tools,
                {"layer": layer_idx, "goal_id": self._goal_id},
            )
            self._layer_results[agent_id] = result or {}
            self._state.evidence_count += 1  # bumped by decorator
            self._state.set_agent_status(agent_id, "success")
            self._event_bus.emit(
                "agent_complete", agent_id=agent_id, layer=layer_idx,
            )
        except asyncio.TimeoutError:
            self._state.set_agent_status(
                agent_id, "error", f"timeout after {agent_config.timeout}s",
            )
            self._event_bus.emit(
                "agent_error", agent_id=agent_id, error="timeout",
            )
        except Exception as exc:
            self._state.set_agent_status(agent_id, "error", str(exc))
            self._event_bus.emit(
                "agent_error", agent_id=agent_id, error=str(exc),
            )

    # ── Prompt building (R1+R2: PromptBuilder reuse) ───────

    def _build_prompt(self, agent_id: str) -> str:
        """Build prompt via PromptBuilder (cached + upstream-formatted)."""
        agent_config = self._get_agent_config(agent_id)
        if agent_config is None:
            return ""

        upstream = {
            dep: self._layer_results[dep]
            for dep in agent_config.input_from
            if dep in self._layer_results
        }

        return self._prompt_builder.build_prompt(
            agent_name=agent_id,
            base_prompt=self._build_goal_context(),
            upstream_outputs=upstream,
        )

    def _build_goal_context(self) -> str:
        """Build goal context block via existing formatter."""
        from .context import get_current_goal_context
        ctx, _ = get_current_goal_context(self._session_id)
        return ctx

    # ── DAG helpers (R11: cached layers) ─────────────────────

    def _get_layers(self) -> list[list[str]]:
        """Compute topological layers from the DAG (cached).

        The YAML config uses ``key: [upstream_deps]`` convention.  The
        DAG function uses ``key: [downstream]`` convention.  We invert
        before calling.
        """
        if self._layers_cache is not None:
            return self._layers_cache

        from ..workflow.dag import topological_layers

        downstream: dict[str, list[str]] = {}
        for node, deps in self._config.dag.items():
            downstream.setdefault(node, [])
            for dep in deps:
                downstream.setdefault(dep, []).append(node)
        self._layers_cache = topological_layers(downstream)
        return self._layers_cache

    def _get_agent_config(self, agent_id: str) -> GoalAgentConfig | None:
        for agent in self._config.agents:
            if agent.id == agent_id:
                return agent
        return None

    def _apply_branches(self, layer: list[str]) -> list[str]:
        if not self._config.branches:
            return layer
        result = []
        skip: set[str] = set()
        for branch in self._config.branches:
            if branch.action == "skip":
                for agent_id in layer:
                    if self._evaluate_condition(branch.condition, agent_id):
                        skip.add(branch.target)
        for agent_id in layer:
            if agent_id not in skip:
                result.append(agent_id)
        return result

    def _evaluate_condition(self, condition: str, agent_id: str) -> bool:
        # Stub — Phase 3 will add DSL evaluation.
        return False

    # ── Completion (R6: Strategy pattern) ────────────────────

    def _check_all_criteria_covered(self) -> bool:
        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return False
        criteria = snapshot.get("criteria", [])
        evidence = snapshot.get("evidence", [])
        evidence_by_criterion: dict[str, list] = {}
        for ev in evidence:
            cid = ev.get("criterion_id")
            if cid:
                evidence_by_criterion.setdefault(cid, []).append(ev)
        for criterion in criteria:
            if not criterion.get("required", True):
                continue
            cid = criterion.get("criterion_id", "")
            if not evidence_by_criterion.get(cid):
                return False
        return True

    async def _auto_complete(self) -> None:
        """Dispatch completion to the configured CompletionStrategy."""
        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return

        from .completion_strategy import CompletionStrategyFactory

        strategy = CompletionStrategyFactory.get(self._config.completion.mode)
        try:
            await strategy.complete(
                self._store,
                self._session_id,
                self._goal_id,
                snapshot.get("criteria", []),
                snapshot.get("evidence", []),
                self._config.name,
            )
            self._state.status = "completed"
            self._event_bus.emit("workflow_completed")
            logger.info("Goal %s completed via %s strategy",
                        self._goal_id, type(strategy).__name__)
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Auto-completion failed: %s", exc)

    # ── Pause / Resume ───────────────────────────────────────

    def pause(self) -> None:
        self._state.paused = True
        self._event_bus.emit("workflow_paused", layer=self._state.current_layer)
        logger.info("Goal workflow paused at layer %d", self._state.current_layer)

    def resume(self) -> None:
        self._state.paused = False
        self._event_bus.emit("workflow_resumed")
        logger.info("Goal workflow resumed from layer %d", self._state.pause_layer)


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