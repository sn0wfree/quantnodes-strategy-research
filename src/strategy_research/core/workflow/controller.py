from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agents import AgentExecutor, AgentRegistry
from .dag import topological_layers
from .types import AgentCall, AgentStatus, RoundResult
from .worker import SwarmWorker, WorkerResult, WorkerStatus

logger = logging.getLogger(__name__)


@dataclass
class ControllerConfig:
    max_retries: int = 3
    timeout_seconds: float = 60.0
    retry_delay: float = 1.0


@dataclass
class AgentExecution:
    call: AgentCall
    status: AgentStatus = AgentStatus.PENDING
    output: dict = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0
    retries: int = 0


@dataclass
class RoundExecution:
    round_num: int
    executions: list[AgentExecution] = field(default_factory=list)
    keep: bool = False
    calmar: float = 0.0
    sharpe: float = 0.0
    total_duration_ms: float = 0.0


class WorkflowController:
    def __init__(
        self,
        registry: AgentRegistry,
        adj: dict[str, list[str]],
        config: ControllerConfig | None = None,
    ) -> None:
        self._registry = registry
        self._adj = adj
        self._config = config or ControllerConfig()
        self._layers = topological_layers(adj)

    @property
    def layers(self) -> list[list[str]]:
        return self._layers

    def build_agent_chain(self) -> list[str]:
        chain: list[str] = []
        for layer in self._layers:
            chain.extend(sorted(layer))
        return chain

    def execute_round(
        self,
        round_num: int,
        base_prompt: str,
        context: dict | None = None,
    ) -> RoundExecution:
        ctx = context or {}
        round_exec = RoundExecution(round_num=round_num)
        start = time.time()

        for layer in self._layers:
            for agent_name in sorted(layer):
                executor = self._registry.get(agent_name)
                if executor is None:
                    exec_ = AgentExecution(
                        call=AgentCall(agent_name=agent_name, prompt="", context=ctx),
                        status=AgentStatus.SKIPPED,
                        error=f"Agent '{agent_name}' not registered",
                    )
                    round_exec.executions.append(exec_)
                    continue

                prompt = self._build_prompt(agent_name, base_prompt, round_exec)
                call = AgentCall(
                    agent_name=agent_name,
                    prompt=prompt,
                    context=self._build_input_from(agent_name, round_exec, ctx),
                )
                exec_ = self._execute_agent(executor, call)
                round_exec.executions.append(exec_)

        round_exec.total_duration_ms = (time.time() - start) * 1000
        return round_exec

    def _build_prompt(
        self,
        agent_name: str,
        base_prompt: str,
        round_exec: RoundExecution,
    ) -> str:
        parts = [base_prompt]

        prev_outputs = []
        for ex in round_exec.executions:
            if ex.status == AgentStatus.SUCCESS and ex.output:
                prev_outputs.append(f"[{ex.call.agent_name}]: {ex.output}")

        if prev_outputs:
            parts.append("\n\nUpstream outputs:\n" + "\n".join(prev_outputs))

        return "\n".join(parts)

    def _build_input_from(
        self,
        agent_name: str,
        round_exec: RoundExecution,
        base_context: dict,
    ) -> dict:
        ctx = dict(base_context)

        upstream = self._get_upstream_agents(agent_name)
        input_data = {}
        for ex in round_exec.executions:
            if ex.call.agent_name in upstream and ex.status == AgentStatus.SUCCESS:
                input_data[ex.call.agent_name] = ex.output

        if input_data:
            ctx["input_from"] = input_data

        return ctx

    def _get_upstream_agents(self, agent_name: str) -> list[str]:
        upstream: list[str] = []
        for src, targets in self._adj.items():
            if agent_name in targets:
                upstream.append(src)
        return upstream

    def _execute_agent(
        self,
        executor: AgentExecutor,
        call: AgentCall,
    ) -> AgentExecution:
        exec_ = AgentExecution(call=call, status=AgentStatus.RUNNING)
        start = time.time()

        for attempt in range(self._config.max_retries):
            try:
                result = executor.run(call.prompt, call.context)
                exec_.output = result
                exec_.status = AgentStatus.SUCCESS
                exec_.duration_ms = (time.time() - start) * 1000
                return exec_
            except Exception as e:
                exec_.retries += 1
                exec_.error = str(e)
                if attempt < self._config.max_retries - 1:
                    time.sleep(self._config.retry_delay)

        exec_.status = AgentStatus.ERROR
        exec_.duration_ms = (time.time() - start) * 1000
        return exec_

    # ── swarm execute_agent (P6 Phase 1-A2) ─────────────────────────

    def execute_agent(
        self,
        agent_call: AgentCall,
        task: str,
        workspace: Path | str | None = None,
    ) -> str:
        """Execute a single swarm agent via SwarmWorker.

        This method is the bridge between ``SwarmRuntime`` (DAG executor)
        and the LLM. It:

            1. Reads the prompt file pointed to by ``agent_call.prompt``
               (relative to ``workspace`` if provided, else a templates
               lookup).
            2. Applies the tool whitelist from ``agent_call.context["tools"]``.
            3. Builds a fresh ``SwarmWorker`` and runs the task.
            4. Returns the worker's summary (or full answer as fallback).

        Parameters
        ----------
        agent_call:
            The DAG node spec. ``agent_call.prompt`` is treated as a path
            to a markdown prompt template (e.g. ``.prompts/researcher.md``).
        task:
            The runtime task string (typically includes upstream context).
        workspace:
            Optional filesystem root for resolving prompt paths and (later)
            for tool invocations. May be a ``Path`` or string.

        Returns
        -------
        str
            A JSON-serialisable dict (or plain text) summarising the worker's
            output. Failures degrade to a dict ``{"status": "error", ...}``
            instead of raising, so DAG layers stay alive.
        """
        ws_path = Path(workspace) if workspace is not None else None

        # 1) Resolve system prompt
        try:
            system_prompt = self._resolve_prompt(agent_call.prompt, ws_path)
        except Exception as exc:                                # noqa: BLE001
            logger.warning("execute_agent: prompt resolution failed: %s", exc)
            return self._error_output(
                f"prompt resolution failed: {exc}",
                agent=agent_call.agent_name,
            )

        # 2) Apply tool whitelist
        try:
            registry = self._build_tool_registry(
                agent_call.context.get("tools", []) if agent_call.context else [],
            )
        except Exception as exc:                                # noqa: BLE001
            logger.warning("execute_agent: tool whitelist failed: %s", exc)
            registry = self._build_tool_registry([])  # fall back to empty

        # 3) Instantiate worker
        try:
            client = self._build_llm_client()
        except Exception as exc:                                # noqa: BLE001
            return self._error_output(
                f"llm client init failed: {exc}",
                agent=agent_call.agent_name,
            )

        worker = SwarmWorker(
            client=client,
            registry=registry,
            system_prompt=system_prompt,
            upstream_context="",  # task string already includes upstream
            max_iterations=20,
            timeout_s=self._config.timeout_seconds,
            temperature=0.3,
            max_tokens=4096,
        )

        # 4) Run
        result: WorkerResult = worker.run(task)

        # 5) Format output
        return self._format_worker_output(result, agent_call.agent_name)

    # ── helpers ─────────────────────────────────────────────────

    def _resolve_prompt(
        self,
        prompt_field: str,
        workspace: Path | None,
    ) -> str:
        """Resolve a prompt_file string into markdown body text.

        Lookup order:
            1. ``workspace / prompt_field`` (if workspace given)
            2. ``templates / prompt_field`` (package default)
        """
        from ... import _TEMPLATES_DIR  # lazy import; package-level constant

        candidates: list[Path] = []
        if workspace is not None:
            candidates.append(workspace / prompt_field)
        candidates.append(_TEMPLATES_DIR / prompt_field)

        for path in candidates:
            try:
                if path.is_file():
                    return path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.debug("prompt read failed for %s: %s", path, exc)

        raise FileNotFoundError(
            f"prompt file not found: {prompt_field!r} "
            f"(tried {', '.join(str(p) for p in candidates)})"
        )

    def _build_tool_registry(self, whitelist: list[str]) -> "object":
        """Build a ToolRegistry filtered by ``whitelist``.

        Falls back to an empty registry if the default registry can't be
        imported (e.g. during minimal scaffolding).
        """
        try:
            from ..agent.tools import ToolRegistry
            from ..agent.builtin_tools import build_default_registry
        except Exception as exc:                                # noqa: BLE001
            logger.debug("default registry import failed: %s", exc)
            from ..agent.tools import ToolRegistry
            return ToolRegistry()

        full = build_default_registry()
        if not whitelist:
            # Empty whitelist = no tools (text-only worker).
            from ..agent.tools import ToolRegistry
            return ToolRegistry()

        # Build a new registry with only the whitelisted tools
        filtered = ToolRegistry()
        for name in whitelist:
            tool = full.get(name)
            if tool is None:
                logger.debug("whitelisted tool not found: %s", name)
                continue
            filtered.register(tool)
        return filtered

    def _build_llm_client(self) -> object:
        """Instantiate an OpenAICompatClient using LLMConfig.load()."""
        from ..llm import LLMConfig, OpenAICompatClient
        cfg = LLMConfig.load()
        return OpenAICompatClient(cfg)

    def _format_worker_output(
        self,
        result: WorkerResult,
        agent_name: str,
    ) -> str:
        """Convert WorkerResult → string for SwarmRuntime consumption."""
        import json
        payload = {
            "agent": agent_name,
            "status": result.status.value,
            "answer": result.answer,
            "summary": result.summary,
            "iterations": result.iterations,
            "tool_calls_made": result.tool_calls_made,
        }
        if result.error:
            payload["error"] = result.error
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _error_output(error: str, agent: str = "") -> str:
        import json
        return json.dumps(
            {"agent": agent, "status": "error", "error": error},
            ensure_ascii=False,
        )
