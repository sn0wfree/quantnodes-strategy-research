"""Goal Workflow Engine — DAG-based research workflow for goals.

Integrates the existing WorkflowController (DAG scheduling) with
GoalStore (state management) to drive structured research processes
defined by YAML configs.

Architecture:
    GoalWorkflowRunner
      ├─ loads GoalWorkflowConfig from YAML
      ├─ creates Goal in GoalStore
      ├─ builds WorkflowController from DAG
      ├─ executes agents layer by layer
      ├─ auto-collects evidence from agent outputs
      └─ auto-completes goal when all criteria are covered

Usage:
    config = load_goal_workflow("factor_research")
    runner = GoalWorkflowRunner(config, session_id="user-123")
    goal_id = await runner.start("研究动量因子在A股的有效性")
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

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
    # agent_id → "pending" | "running" | "success" | "error" | "skipped"
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

    def __init__(self, session_id: str, goal_id: str):
        self._session_id = session_id
        self._goal_id = goal_id

    def collect(
        self,
        agent_id: str,
        result: dict[str, Any],
        criterion_idx: int,
    ) -> int:
        """Append agent output as evidence. Returns number of evidence added."""
        from .models import EvidenceInput
        from .store import GoalStore

        answer = result.get("answer", "")
        if not answer or len(answer.strip()) < 10:
            return 0

        # Resolve criterion_id from index
        store = GoalStore()
        snapshot = store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return 0

        criteria = snapshot.get("criteria", [])
        if criterion_idx < 0 or criterion_idx >= len(criteria):
            return 0

        criterion_id = criteria[criterion_idx].get("criterion_id")
        if not criterion_id:
            return 0

        # Truncate long evidence
        text = answer[:2000]

        try:
            store.append_evidence(
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

    Orchestrates agent execution through the existing WorkflowController,
    collecting evidence and auto-completing the goal when done.
    """

    def __init__(
        self,
        config: GoalWorkflowConfig,
        session_id: str,
        *,
        agent_runner: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            config: Workflow configuration loaded from YAML.
            session_id: Current session id.
            agent_runner: Optional async callable ``(agent_id, prompt, tools, context)
                         -> dict`` for executing agents. When None, uses a stub
                         that returns empty results.
        """
        self._config = config
        self._session_id = session_id
        self._agent_runner = agent_runner
        self._state = GoalWorkflowState()
        self._goal_id: str = ""
        self._evidence_collector: GoalEvidenceCollector | None = None
        self._layer_results: dict[str, dict[str, Any]] = {}

    @property
    def state(self) -> GoalWorkflowState:
        """Current workflow state."""
        return self._state

    @property
    def goal_id(self) -> str:
        """Goal id created by this workflow."""
        return self._goal_id

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
        """Start the workflow: create goal → execute DAG → return goal_id.

        Args:
            objective: Research goal objective text.

        Returns:
            The created goal id.
        """
        from .context import default_goal_criteria
        from .models import RiskTier
        from .store import GoalStore

        self._state.status = "running"
        self._state.start_time = time.time()

        # 1. Create the goal
        criteria = self._config.goal.default_criteria or default_goal_criteria()
        risk_tier = RiskTier(self._config.goal.risk_tier)

        store = GoalStore()
        goal = store.replace_goal(
            session_id=self._session_id,
            objective=objective,
            criteria=criteria,
            source="workflow",
            protocol=self._config.name,
            risk_tier=risk_tier,
        )
        self._goal_id = goal.goal_id
        self._evidence_collector = GoalEvidenceCollector(
            self._session_id, self._goal_id
        )

        logger.info(
            "Goal workflow started: goal_id=%s workflow=%s",
            self._goal_id, self._config.name,
        )

        # 2. Execute the DAG
        try:
            await self._execute_dag()
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            logger.error("Goal workflow failed: %s", exc)

        return self._goal_id

    async def _execute_dag(self) -> None:
        """Execute the DAG layer by layer."""
        layers = self._get_layers()

        for layer_idx, layer in enumerate(layers):
            # Check pause
            if self._state.paused:
                self._state.pause_layer = layer_idx
                self._state.status = "paused"
                return

            self._state.current_layer = layer_idx

            # Apply branch conditions
            layer = self._apply_branches(layer)

            # Execute agents in this layer
            await self._execute_layer(layer, layer_idx)

            # Check auto-completion
            if self._config.completion.mode == "auto":
                if self._check_all_criteria_covered():
                    await self._auto_complete()
                    return

        # All layers done
        if self._config.completion.mode == "auto":
            await self._auto_complete()
        else:
            self._state.status = "completed"

    async def _execute_layer(
        self, layer: list[str], layer_idx: int
    ) -> None:
        """Execute all agents in a layer (in parallel)."""
        if not layer:
            return

        tasks = [
            self._execute_agent(agent_id, layer_idx)
            for agent_id in layer
        ]
        await asyncio.gather(*tasks)

    async def _execute_agent(
        self, agent_id: str, layer_idx: int
    ) -> None:
        """Execute a single agent, retry on failure, collect evidence."""
        agent_config = self._get_agent_config(agent_id)
        if agent_config is None:
            self._state.set_agent_status(agent_id, "skipped", "not in config")
            return

        self._state.set_agent_status(agent_id, "running")

        for attempt in range(agent_config.max_retries + 1):
            try:
                # Build prompt with upstream outputs
                prompt = self._build_prompt(agent_id)

                # Execute agent
                result = await self._run_agent(
                    agent_config, prompt, layer_idx
                )

                # Collect evidence
                if self._evidence_collector and result:
                    count = self._evidence_collector.collect(
                        agent_id, result, agent_config.evidence_criterion
                    )
                    self._state.evidence_count += count

                # Store result for upstream gathering
                self._layer_results[agent_id] = result or {}

                self._state.set_agent_status(agent_id, "success")
                return

            except asyncio.TimeoutError:
                self._state.set_agent_status(
                    agent_id, "error", f"timeout after {agent_config.timeout}s"
                )
                if attempt < agent_config.max_retries:
                    logger.warning(
                        "Agent %s timed out (attempt %d), retrying...",
                        agent_id, attempt + 1,
                    )
                    await asyncio.sleep(1.0)
                else:
                    return

            except Exception as exc:
                self._state.set_agent_status(agent_id, "error", str(exc))
                if attempt < agent_config.max_retries:
                    logger.warning(
                        "Agent %s failed (attempt %d): %s, retrying...",
                        agent_id, attempt + 1, exc,
                    )
                    await asyncio.sleep(1.0)
                else:
                    return

    async def _run_agent(
        self,
        agent_config: GoalAgentConfig,
        prompt: str,
        layer_idx: int,
    ) -> dict[str, Any]:
        """Run an agent via the configured runner or a stub."""
        if self._agent_runner is not None:
            return await asyncio.wait_for(
                self._agent_runner(
                    agent_config.id,
                    prompt,
                    agent_config.tools,
                    {"layer": layer_idx, "goal_id": self._goal_id},
                ),
                timeout=agent_config.timeout,
            )

        # Stub: return empty result
        await asyncio.sleep(0.01)
        return {"answer": f"[stub] {agent_config.id} completed"}

    # ── Prompt Building ──────────────────────────────────────

    def _build_prompt(self, agent_id: str) -> str:
        """Build prompt for an agent, injecting upstream outputs."""
        agent_config = self._get_agent_config(agent_id)
        if agent_config is None:
            return ""

        # Load base prompt from template
        base_prompt = self._load_prompt_template(agent_config.prompt_file)

        # Gather upstream outputs
        upstream = {}
        for dep in agent_config.input_from:
            if dep in self._layer_results:
                upstream[dep] = self._layer_results[dep]

        # Inject goal context
        goal_context = self._build_goal_context()

        # Assemble final prompt
        parts = [goal_context]
        if base_prompt:
            parts.append(base_prompt)
        if upstream:
            parts.append("\n## 上游 Agent 输出\n")
            for dep_name, dep_output in upstream.items():
                answer = dep_output.get("answer", "(no output)")
                parts.append(f"### {dep_name}\n{answer[:1500]}\n")

        return "\n".join(parts)

    def _build_goal_context(self) -> str:
        """Build goal context block for the agent prompt."""
        from .context import get_current_goal_context
        ctx, _ = get_current_goal_context(self._session_id)
        return ctx

    def _load_prompt_template(self, prompt_file: str) -> str:
        """Load a prompt template from the templates directory."""
        from pathlib import Path

        # Resolve path relative to templates directory
        templates_dir = Path(__file__).parent.parent.parent / "templates"
        prompt_path = templates_dir / prompt_file

        if not prompt_path.exists():
            logger.warning("Prompt template not found: %s", prompt_path)
            return ""

        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to load prompt %s: %s", prompt_path, exc)
            return ""

    # ── DAG Helpers ──────────────────────────────────────────

    def _get_layers(self) -> list[list[str]]:
        """Compute topological layers from the DAG.

        The YAML config uses ``key: [upstream_deps]`` convention
        (node depends on its list values).  The DAG function uses
        ``key: [downstream]`` convention (key points to nodes that
        depend on it).  We invert before calling.
        """
        from ..workflow.dag import topological_layers

        # Invert: upstream_deps → downstream
        downstream: dict[str, list[str]] = {}
        for node, deps in self._config.dag.items():
            downstream.setdefault(node, [])
            for dep in deps:
                downstream.setdefault(dep, []).append(node)

        return topological_layers(downstream)

    def _get_agent_config(self, agent_id: str) -> GoalAgentConfig | None:
        """Find agent config by id."""
        for agent in self._config.agents:
            if agent.id == agent_id:
                return agent
        return None

    def _apply_branches(self, layer: list[str]) -> list[str]:
        """Apply conditional branches to filter/modify a layer."""
        if not self._config.branches:
            return layer

        result = []
        skip = set()

        for branch in self._config.branches:
            if branch.action == "skip":
                # Check if any agent in the layer triggers the skip
                for agent_id in layer:
                    if self._evaluate_condition(branch.condition, agent_id):
                        skip.add(branch.target)

        for agent_id in layer:
            if agent_id not in skip:
                result.append(agent_id)

        return result

    def _evaluate_condition(self, condition: str, agent_id: str) -> bool:
        """Evaluate a branch condition against an agent's output.

        This is a simplified evaluator.  Conditions are strings like
        ``"factor_analyst.output.sharpe < 0.3"``.
        """
        # For now, return False (no branches triggered)
        # Full implementation would parse and evaluate the expression
        return False

    # ── Completion ───────────────────────────────────────────

    def _check_all_criteria_covered(self) -> bool:
        """Check if all required criteria have evidence."""
        from .store import GoalStore

        store = GoalStore()
        snapshot = store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return False

        criteria = snapshot.get("criteria", [])
        evidence = snapshot.get("evidence", [])

        # Group evidence by criterion
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
        """Auto-complete the goal after all criteria are covered."""
        from .models import AuditRow, GoalStatus
        from .store import GoalStore

        store = GoalStore()
        snapshot = store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return

        criteria = snapshot.get("criteria", [])
        evidence = snapshot.get("evidence", [])

        if self._config.completion.mode == "lite":
            try:
                store.complete_lite(
                    session_id=self._session_id,
                    goal_id=self._goal_id,
                    expected_goal_id=self._goal_id,
                    recap=f"Workflow {self._config.name} auto-completed (lite)",
                )
                self._state.status = "completed"
                logger.info("Goal %s auto-completed (lite)", self._goal_id)
            except Exception as exc:
                logger.error("Lite completion failed: %s", exc)
                self._state.status = "error"
                self._state.error_message = str(exc)
        else:
            # Build audit rows
            audit_rows = []
            evidence_ids = [e.get("evidence_id", "") for e in evidence]
            for criterion in criteria:
                if not criterion.get("required", True):
                    continue
                cid = criterion.get("criterion_id", "")
                # Find evidence for this criterion
                cid_evidence_ids = [
                    e.get("evidence_id", "") for e in evidence
                    if e.get("criterion_id") == cid
                ]
                audit_rows.append(AuditRow(
                    criterion_id=cid,
                    result="satisfied",
                    evidence_ids=cid_evidence_ids or evidence_ids[:1],
                    notes=f"Auto-completed by workflow {self._config.name}",
                ))

            try:
                store.update_status(
                    session_id=self._session_id,
                    goal_id=self._goal_id,
                    expected_goal_id=self._goal_id,
                    status=GoalStatus.COMPLETE,
                    audit=audit_rows,
                    recap=f"Workflow {self._config.name} auto-completed",
                )
                self._state.status = "completed"
                logger.info("Goal %s auto-completed", self._goal_id)
            except Exception as exc:
                logger.error("Auto-completion failed: %s", exc)
                self._state.status = "error"
                self._state.error_message = str(exc)

    # ── Pause/Resume ─────────────────────────────────────────

    def pause(self) -> None:
        """Pause workflow execution after the current agent finishes."""
        self._state.paused = True
        logger.info("Goal workflow paused at layer %d", self._state.current_layer)

    def resume(self) -> None:
        """Resume workflow execution."""
        self._state.paused = False
        logger.info("Goal workflow resumed from layer %d", self._state.pause_layer)

    async def continue_after_pause(self) -> None:
        """Resume execution from the paused layer."""
        if not self._state.paused:
            return
        self._state.paused = False
        self._state.status = "running"

        # Continue from paused layer
        layers = self._get_layers()
        for layer_idx in range(self._state.pause_layer, len(layers)):
            if self._state.paused:
                self._state.pause_layer = layer_idx
                return

            self._state.current_layer = layer_idx
            layer = self._apply_branches(layers[layer_idx])
            await self._execute_layer(layer, layer_idx)

            if self._config.completion.mode == "auto":
                if self._check_all_criteria_covered():
                    await self._auto_complete()
                    return

        if self._config.completion.mode == "auto":
            await self._auto_complete()
        else:
            self._state.status = "completed"


__all__ = [
    "GoalWorkflowConfig",
    "GoalWorkflowGoalConfig",
    "GoalAgentConfig",
    "CompletionConfig",
    "BranchConfig",
    "GoalWorkflowState",
    "GoalEvidenceCollector",
    "GoalWorkflowRunner",
]
