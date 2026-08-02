"""AgentRunner — pluggable agent execution for Goal Workflow.

FIXME(architecture): module is effectively dormant — no production
callers. The Goal Workflow executes agents via ``SwarmRuntime`` /
WorkflowController instead. ``agent_runner`` / ``agent_runner_type``
are still accepted (deprecated) kwargs in workflow.py for back-compat
(see docs/phase-4-plan.md "agent_runner 弃用参数") and the goal to
drop them. NOTE: ``AgentLoopRunner.run`` calls ``build_agent_loop``
with ``llm_client=``/``tools=`` kwargs that don't match the current
signature — it would TypeError if ever used; do NOT wire it up without
fixing the call. Keep AgentRunnerFactory/Registry only if the plug-in
runner abstraction is revived.

Defines the AgentRunner Protocol and 3 default implementations:
  - StubAgentRunner: returns stub results (tests/CI)
  - SwarmWorkerRunner: uses WorkflowController.execute_agent
  - AgentLoopRunner: uses build_agent_loop + AgentLoop.run()

Plus AgentRunnerFactory + AgentRunnerRegistry for plug-in selection.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AgentRunner(Protocol):
    """Pluggable agent execution interface.

    Implementations execute a single agent node and return its output.
    """

    async def run(
        self,
        agent_id: str,
        prompt: str,
        tools: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the agent and return ``{"answer": str, ...}``."""
        ...


# ── Built-in Implementations ─────────────────────────────────


class StubAgentRunner:
    """Returns stub results — for tests, dry-runs, and CI."""

    async def run(
        self,
        agent_id: str,
        prompt: str,
        tools: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {"answer": f"[stub] {agent_id} completed"}


class SwarmWorkerRunner:
    """Uses WorkflowController.execute_agent (mini-ReAct via SwarmWorker)."""

    def __init__(self, controller: Any | None = None):
        self._controller = controller

    def _get_controller(self) -> Any:
        if self._controller is None:
            from .agents import AgentRegistry
            from .controller import ControllerConfig, WorkflowController
            cfg = ControllerConfig(timeout_seconds=60.0)
            self._controller = WorkflowController(
                registry=AgentRegistry(), adj={}, config=cfg,
            )
        return self._controller

    async def run(
        self,
        agent_id: str,
        prompt: str,
        tools: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        from .types import AgentCall

        controller = self._get_controller()
        call = AgentCall(
            agent_name=agent_id,
            prompt=prompt,
            context={"tools": tools},
        )
        # WorkflowController.execute_agent is synchronous; run in thread
        result = await asyncio.to_thread(
            controller.execute_agent, call, prompt, None
        )
        if isinstance(result, dict):
            return result
        return {"answer": str(result)}


class AgentLoopRunner:
    """Uses build_agent_loop + AgentLoop.run() (full-featured ReAct)."""

    def __init__(self, llm_client: Any):
        self._llm_client = llm_client

    async def run(
        self,
        agent_id: str,
        prompt: str,
        tools: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        from ..agent.role_factory import build_agent_loop

        loop = build_agent_loop(
            role=agent_id,
            llm_client=self._llm_client,
            tools=tools,
        )
        result = await loop.arun(prompt)
        return {
            "answer": getattr(result, "answer", ""),
            "summary": getattr(result, "summary", ""),
        }


# ── Factory + Registry ───────────────────────────────────────


class AgentRunnerFactory:
    """Factory: create AgentRunner instances by type name."""

    _BUILDERS: dict[str, Any] = {
        "stub": lambda **kw: StubAgentRunner(),
        "swarm_worker": lambda **kw: SwarmWorkerRunner(
            controller=kw.get("controller"),
        ),
        "agent_loop": lambda **kw: AgentLoopRunner(
            llm_client=kw.get("llm_client"),
        ),
    }

    @classmethod
    def create(cls, runner_type: str = "stub", **kwargs) -> AgentRunner:
        """Create a runner by type name.

        Args:
            runner_type: One of "stub", "swarm_worker", "agent_loop".
            **kwargs: Pass-through args (controller, llm_client).

        Raises:
            ValueError: If runner_type is unknown.
        """
        if runner_type not in cls._BUILDERS:
            raise ValueError(
                f"Unknown runner type: {runner_type!r}. "
                f"Valid: {list(cls._BUILDERS.keys())}"
            )
        return cls._BUILDERS[runner_type](**kwargs)

    @classmethod
    def list_types(cls) -> list[str]:
        """List all registered runner types."""
        return list(cls._BUILDERS.keys())


class AgentRunnerRegistry:
    """Mutable registry for custom AgentRunner classes.

    Use this to register new runner types at runtime.  Built-in types
    are always available via AgentRunnerFactory.
    """

    _runners: dict[str, type[AgentRunner]] = {}

    @classmethod
    def register(cls, name: str, runner_class: type[AgentRunner]) -> None:
        """Register a new runner class."""
        if not isinstance(runner_class, type):
            raise TypeError(f"Expected class, got {type(runner_class)}")
        cls._runners[name] = runner_class

    @classmethod
    def create(cls, name: str, **kwargs) -> AgentRunner:
        """Create a registered runner instance."""
        if name not in cls._runners:
            raise ValueError(
                f"Unknown runner: {name!r}. "
                f"Registered: {list(cls._runners.keys())}"
            )
        return cls._runners[name](**kwargs)

    @classmethod
    def list_registered(cls) -> list[str]:
        """List all registered runner class names."""
        return list(cls._runners.keys())


__all__ = [
    "AgentRunner",
    "StubAgentRunner",
    "SwarmWorkerRunner",
    "AgentLoopRunner",
    "AgentRunnerFactory",
    "AgentRunnerRegistry",
]
