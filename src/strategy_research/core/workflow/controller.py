from __future__ import annotations

import time
from dataclasses import dataclass, field

from .agents import AgentExecutor, AgentRegistry
from .dag import topological_layers
from .types import AgentCall, AgentStatus, RoundResult


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
