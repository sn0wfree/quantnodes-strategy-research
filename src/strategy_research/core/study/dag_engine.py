"""DAG engine for study round execution.

Extracted from runner.py to reduce file size and improve modularity.
Uses AgentExecutor with topological layer-by-layer serial execution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .engine_common import build_agent_ctx, safe_json_loads, phase_emitter, save_agent_outputs

logger = logging.getLogger(__name__)


def run_round_dag(
    runner: Any,
    path: Path,
    strategy: str,
    current_state: dict,
    run_dir: Path,
    graph: Any,
    *,
    session: str,
    sid: str,
    round_num: int,
    directive_text: str | None,
) -> dict:
    """Execute one round by driving graph.json through AgentExecutor.

    Serial execution (topological layer by layer, one agent at a time).
    The returned dict matches the legacy ``exec_result + eval_result``
    schema so downstream code is untouched.
    """
    from ..agent.dag_config import AgentDAGConfig
    from ..agent.executor import AgentExecutor
    from ..agent.registry import get_default_registry

    dag_config = AgentDAGConfig.from_study_graph(
        graph, name=f"study_{sid}_r{round_num}",
        description=runner._get_study().objective,
    )
    registry = getattr(runner, "_plugin_registry", None) or get_default_registry()
    executor = AgentExecutor(registry)
    task_text = runner._build_round_task_text(current_state, directive_text)
    agent_ctx = build_agent_ctx(strategy, run_dir, session, runner)

    layers = runner._layered_topological_layers(graph)
    agent_outputs: dict[str, Any] = {}
    node_map = dag_config.node_map()

    for layer_idx, layer_ids in enumerate(layers):
        with phase_emitter(runner._emit, session, sid, round_num, f"layer_{layer_idx}"):
            upstream: dict[str, str] = {}
            for agent_id in layer_ids:
                plugin = registry.get(agent_id)
                if plugin is None:
                    logger.warning(
                        "study %s round %d: unknown plugin %r; skipping",
                        sid, round_num, agent_id,
                    )
                    continue
                node = node_map.get(agent_id)
                result = executor.execute(
                    plugin, task_text, path,
                    context=agent_ctx,
                    upstream_outputs=upstream,
                    node=node,
                )
                if result.status == "success":
                    agent_outputs[agent_id] = safe_json_loads(result.output, fallback=result.output)
                    upstream[agent_id] = result.output
                    runner._save_agent_output(run_dir, agent_id, result)
                else:
                    agent_outputs[agent_id] = {
                        "error": result.error or "unknown error",
                        "parse_failed": True,
                    }
                    upstream[agent_id] = result.output or "{}"
                    runner._save_agent_output(run_dir, agent_id, result)
                runner._emit(session, "study_agent_complete", {
                    "study_id": sid, "round": round_num,
                    "agent": agent_id,
                    "status": result.status,
                    "elapsed_s": result.elapsed_s,
                })

    return runner._rebuild_phase_outputs(agent_outputs, graph)
