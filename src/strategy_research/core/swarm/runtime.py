"""SwarmRuntime — DAG-based multi-agent orchestration with hook support (P3.1).

Breaking change in Phase 3:
  ``execute()`` now accepts an optional ``hooks`` parameter that enables
  external code to observe and control the execution lifecycle.

Hook callbacks:
  - ``on_layer_start(layer_idx, agents, context)``
  - ``on_agent_complete(agent_id, result, context)``
  - ``on_layer_complete(layer_idx, agents, results)``
  - ``should_stop() -> bool``
"""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..workflow.agents import AgentRegistry
from ..workflow.controller import WorkflowController
from ..workflow.grounding import GroundingProvider
from ..workflow.types import AgentCall, AgentStatus, SwarmHook

logger = logging.getLogger(__name__)


# ── Default controller factory ────────────────────────────────


def _build_default_controller() -> WorkflowController | None:
    """Build a default WorkflowController backed by SwarmWorker + LLM."""
    try:
        from ..workflow.controller import ControllerConfig
        cfg = ControllerConfig(timeout_seconds=60.0)
        return WorkflowController(registry=AgentRegistry(), adj={}, config=cfg)
    except Exception as exc:                                    # noqa: BLE001
        logger.debug("default controller init failed: %s", exc)
        return None


# ── Data classes ───────────────────────────────────────────────


@dataclass
class SwarmPreset:
    """A swarm preset loaded from YAML.

    Unified preset type (P3.3): covers both generic swarm presets
    and goal-specific workflow presets.  The goal/completion/branches
    fields are optional — generic swarm presets leave them as None.

    This replaces the separate ``GoalWorkflowConfig`` type for the
    execution layer.  The goal workflow loader still produces the
    richer ``GoalWorkflowConfig`` for YAML parsing, but converts
    it to ``SwarmPreset`` before passing to SwarmRuntime.
    """

    name: str
    description: str = ""
    agents: list[AgentCall] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)
    # Goal-specific fields (None for generic swarm presets)
    goal: dict[str, Any] | None = None
    completion: dict[str, Any] | None = None
    branches: list[dict[str, Any]] = field(default_factory=list)
    version: str = "1.0"


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


# ── SwarmRuntime ──────────────────────────────────────────────


class SwarmRuntime:
    """DAG-based multi-agent swarm runtime with hook support.

    Usage:
        runtime = SwarmRuntime(controller=ctrl)
        preset = load_preset(preset_path)
        result = runtime.execute(preset, workspace, task, hooks=[my_hook])
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
        self._owns_default_controller = controller is None

    def execute(
        self,
        preset: SwarmPreset,
        workspace: Path,
        task: str,
        hooks: list[SwarmHook] | None = None,
    ) -> SwarmResult:
        """Execute a swarm preset with optional lifecycle hooks.

        Args:
            preset: Swarm preset defining agents + DAG.
            workspace: Filesystem root for prompt files.
            task: The task prompt string.
            hooks: Optional list of SwarmHook instances to call
                   at each lifecycle point.

        Returns:
            SwarmResult with per-agent outputs and success flag.
        """
        run_id = f"swarm_{uuid.uuid4().hex[:8]}"
        self._active_runs[run_id] = True
        hooks = hooks or []

        result = SwarmResult(run_id=run_id, preset_name=preset.name)
        t0 = time.perf_counter()

        try:
            layers = self._topological_layers(preset.dag)

            for layer_idx, layer in enumerate(layers):
                if run_id not in self._active_runs:
                    break  # cancelled via cancel(run_id)

                # ── Hook: on_layer_start
                self._emit(hooks, "on_layer_start", layer_idx, layer, {})

                layer_futures: dict[Any, str] = {}
                with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                    for agent_id in layer:
                        agent_call = self._find_agent(preset.agents, agent_id)
                        if agent_call is None:
                            continue

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

                            # ── Hook: on_agent_complete
                            self._emit(
                                hooks, "on_agent_complete",
                                agent_id, agent_result, {},
                            )

                        except Exception as exc:  # noqa: BLE001
                            result.agent_results[agent_id] = AgentResult(
                                agent_id=agent_id,
                                status=AgentStatus.FAILED,
                                error=str(exc),
                            )
                            self._emit(
                                hooks, "on_agent_complete",
                                agent_id,
                                result.agent_results[agent_id],
                                {},
                            )

                # ── Hook: on_layer_complete
                self._emit(
                    hooks, "on_layer_complete",
                    layer_idx, layer,
                    {aid: r for aid, r in result.agent_results.items()},
                )

                # ── Hook: should_stop
                if self._any_hook_should_stop(hooks):
                    logger.info("SwarmRuntime stopped by hook at layer %d", layer_idx)
                    break

            result.success = all(
                r.status == AgentStatus.SUCCESS
                for r in result.agent_results.values()
            )
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

    # ── Hook helpers ───────────────────────────────────────────

    def _emit(
        self,
        hooks: list[SwarmHook],
        method_name: str,
        *args: Any,
    ) -> None:
        """Call a hook method on all hooks, swallowing errors."""
        for hook in hooks:
            try:
                method = getattr(hook, method_name, None)
                if method is not None:
                    method(*args)
            except Exception as exc:                    # noqa: BLE001
                logger.warning(
                    "Hook %s.%s failed: %s",
                    getattr(hook, "name", type(hook).__name__),
                    method_name, exc,
                )

    def _any_hook_should_stop(self, hooks: list[SwarmHook]) -> bool:
        """Return True if any hook's should_stop() returns True."""
        for hook in hooks:
            try:
                if hook.should_stop():
                    return True
            except Exception as exc:                    # noqa: BLE001
                logger.warning("Hook %s.should_stop failed: %s", hook.name, exc)
        return False

    # ── Agent execution ────────────────────────────────────────

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
            # P1.5: Use PromptBuilder for structured prompt construction
            from ..workflow.prompt import PromptBuilder
            builder = PromptBuilder()
            full_task = builder.build_prompt(
                agent_name=agent_call.agent_name,
                base_prompt=task,
                context=agent_call.context if hasattr(agent_call, "context") else None,
                upstream_outputs=upstream if upstream else None,
            )

            if self._controller is None and self._owns_default_controller:
                self._controller = _build_default_controller()

            if self._controller is not None:
                if self._owns_default_controller:
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
                    output = self._controller.execute_agent(
                        agent_call, full_task, workspace,
                    )
            else:
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

    # ── DAG helpers ────────────────────────────────────────────

    def _find_agent(self, agents: list[AgentCall], agent_id: str) -> AgentCall | None:
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
        upstream_ids = dag.get(agent_id, [])
        upstream = {}
        for uid in upstream_ids:
            r = results.get(uid)
            if r and r.status == AgentStatus.SUCCESS:
                upstream[uid] = r.output
        return upstream

    def _topological_layers(self, dag: dict[str, list[str]]) -> list[list[str]]:
        """Split DAG into execution layers via topological sort."""
        in_degree: dict[str, int] = {node: 0 for node in dag}
        for node, deps in dag.items():
            for dep in deps:
                if dep not in in_degree:
                    in_degree[dep] = 0
                in_degree[node] = len(deps)

        layers: list[list[str]] = []
        remaining = set(dag.keys())

        while remaining:
            layer = [n for n in remaining if in_degree.get(n, 0) == 0]
            if not layer:
                layers.append(list(remaining))
                break

            layers.append(sorted(layer))
            for n in layer:
                remaining.discard(n)
                for node, deps in dag.items():
                    if n in deps:
                        in_degree[node] = max(0, in_degree[node] - 1)

        return layers