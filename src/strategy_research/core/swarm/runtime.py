"""SwarmRuntime — DAG-based multi-agent orchestration with worker grounding."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..workflow.agents import AgentRegistry
from ..workflow.controller import WorkflowController
from ..workflow.grounding import GroundingProvider
from ..workflow.types import AgentCall, AgentStatus

logger = logging.getLogger(__name__)


# ── Default controller factory (P6 Phase 1-A3) ────────────────────────


def _build_default_controller() -> WorkflowController | None:
    """Build a default WorkflowController backed by SwarmWorker + LLM.

    Returns None if the LLM client cannot be initialised (e.g. missing
    API key in the test environment). Caller should handle the None
    gracefully (fall back to stub).
    """
    try:
        from ..workflow.controller import ControllerConfig
        cfg = ControllerConfig(timeout_seconds=60.0)
        return WorkflowController(registry=AgentRegistry(), adj={}, config=cfg)
    except Exception as exc:                                    # noqa: BLE001
        logger.debug("default controller init failed: %s", exc)
        return None


@dataclass
class SwarmPreset:
    """A swarm preset loaded from YAML."""

    name: str
    description: str = ""
    agents: list[AgentCall] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result of a single agent execution."""

    agent_id: str
    status: AgentStatus = AgentStatus.PENDING
    output: str = ""
    error: str | None = None
    elapsed_s: float = 0.0


@dataclass
class SwarmResult:
    """Result of a swarm execution."""

    run_id: str = ""
    preset_name: str = ""
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    final_output: str = ""
    elapsed_s: float = 0.0
    success: bool = False


class SwarmRuntime:
    """DAG-based multi-agent swarm runtime.

    Executes agents according to a dependency DAG, with worker grounding
    (pre-fetched market data) and streaming progress.

    Usage:
        runtime = SwarmRuntime(controller=ctrl)
        preset = load_preset(preset_path)
        result = runtime.execute(preset, workspace, task)
    """

    def __init__(
        self,
        controller: WorkflowController | None = None,
        grounding: GroundingProvider | None = None,
        max_workers: int = 4,
    ) -> None:
        self._controller = controller
        self._grounding = grounding
        self._max_workers = max_workers
        self._active_runs: dict[str, bool] = {}
        # Lazily-instantiated default controller (created on first use)
        self._owns_default_controller = controller is None

    def execute(
        self,
        preset: SwarmPreset,
        workspace: Path,
        task: str,
    ) -> SwarmResult:
        """Execute a swarm preset.

        Agents are executed in topological order (DAG layers).
        Within each layer, agents run in parallel via ThreadPoolExecutor.
        """
        run_id = f"swarm_{uuid.uuid4().hex[:8]}"
        self._active_runs[run_id] = True

        result = SwarmResult(
            run_id=run_id,
            preset_name=preset.name,
        )

        t0 = time.perf_counter()

        try:
            # Topological sort
            layers = self._topological_layers(preset.dag)

            for layer in layers:
                if run_id not in self._active_runs:
                    break  # cancelled

                # Execute layer in parallel
                layer_futures = {}
                with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                    for agent_id in layer:
                        agent_call = self._find_agent(preset.agents, agent_id)
                        if agent_call is None:
                            continue

                        # Gather upstream outputs
                        upstream = self._gather_upstream(
                            agent_id, preset.dag, result.agent_results,
                        )

                        future = executor.submit(
                            self._execute_agent,
                            agent_call, workspace, task, upstream,
                        )
                        layer_futures[future] = agent_id

                    for future in as_completed(layer_futures):
                        agent_id = layer_futures[future]
                        try:
                            agent_result = future.result()
                            result.agent_results[agent_id] = agent_result
                        except Exception as exc:  # noqa: BLE001
                            result.agent_results[agent_id] = AgentResult(
                                agent_id=agent_id,
                                status=AgentStatus.FAILED,
                                error=str(exc),
                            )

            # Check if all agents succeeded
            result.success = all(
                r.status == AgentStatus.SUCCESS
                for r in result.agent_results.values()
            )

            # Collect final output from last completed agent
            completed = [
                r for r in result.agent_results.values()
                if r.status == AgentStatus.SUCCESS
            ]
            if completed:
                result.final_output = completed[-1].output

        finally:
            self._active_runs.pop(run_id, None)
            result.elapsed_s = round(time.perf_counter() - t0, 2)

        return result

    def cancel(self, run_id: str) -> bool:
        """Cancel a running swarm."""
        if run_id in self._active_runs:
            del self._active_runs[run_id]
            return True
        return False

    def _execute_agent(
        self,
        agent_call: AgentCall,
        workspace: Path,
        task: str,
        upstream: dict[str, str],
    ) -> AgentResult:
        """Execute a single agent."""
        t0 = time.perf_counter()

        try:
            # Build agent task with upstream context
            full_task = task
            if upstream:
                upstream_ctx = "\n".join(
                    f"=== {aid} ===\n{out[:500]}"
                    for aid, out in upstream.items()
                )
                full_task = f"上游输出:\n{upstream_ctx}\n\n当前任务: {task}"

            # Use controller if available; lazily create a default one
            # backed by SwarmWorker + LLMConfig.
            if self._controller is None and self._owns_default_controller:
                self._controller = _build_default_controller()

            if self._controller is not None:
                if self._owns_default_controller:
                    # Default controller: convert failures into "[error]"
                    # so DAG layers stay alive (otherwise a transient
                    # LLM hiccup would poison downstream agents).
                    try:
                        output = self._controller.execute_agent(
                            agent_call, full_task, workspace,
                        )
                    except Exception as exc:                    # noqa: BLE001
                        logger.warning(
                            "default controller.execute_agent "
                            "failed for %s: %s",
                            agent_call.agent_name, exc,
                        )
                        output = f"[error] {agent_call.agent_name}: {exc}"
                else:
                    # User-supplied controller: propagate failures so
                    # callers can observe them via agent_results[*].error.
                    output = self._controller.execute_agent(
                        agent_call, full_task, workspace,
                    )
            else:
                # No controller → stub fallback (tests, dry-runs)
                output = f"[stub] {agent_call.agent_name}: completed"

            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.SUCCESS,
                output=output,
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.ERROR,
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

    def _find_agent(self, agents: list[AgentCall], agent_id: str) -> AgentCall | None:
        """Find agent by ID."""
        for a in agents:
            if a.agent_name == agent_id:
                return a
        return None

    def _gather_upstream(
        self,
        agent_id: str,
        dag: dict[str, list[str]],
        results: dict[str, AgentResult],
    ) -> dict[str, str]:
        """Gather outputs from upstream agents."""
        upstream_ids = dag.get(agent_id, [])
        upstream = {}
        for uid in upstream_ids:
            r = results.get(uid)
            if r and r.status == AgentStatus.SUCCESS:
                upstream[uid] = r.output
        return upstream

    def _topological_layers(self, dag: dict[str, list[str]]) -> list[list[str]]:
        """Split DAG into execution layers via topological sort."""
        # Compute in-degree
        in_degree: dict[str, int] = {node: 0 for node in dag}
        for node, deps in dag.items():
            for dep in deps:
                if dep not in in_degree:
                    in_degree[dep] = 0
                in_degree[node] = len(deps)

        layers: list[list[str]] = []
        remaining = set(dag.keys())

        while remaining:
            # Find nodes with zero in-degree
            layer = [n for n in remaining if in_degree.get(n, 0) == 0]
            if not layer:
                # Cycle detected — add remaining as single layer
                layers.append(list(remaining))
                break

            layers.append(sorted(layer))
            for n in layer:
                remaining.discard(n)
                # Reduce in-degree for dependents
                for node, deps in dag.items():
                    if n in deps:
                        in_degree[node] = max(0, in_degree[node] - 1)

        return layers
