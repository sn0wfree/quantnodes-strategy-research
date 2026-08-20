"""LangGraph engine for study round execution.

Requires ``langgraph`` extra: ``pip install strategy-research[langgraph]``.

Converts a ``StudyGraph`` into a LangGraph ``StateGraph``, executes agents
via ``AgentExecutor``, and returns the legacy ``exec_result + eval_result``
schema so downstream callers (manifest, budget, review, state.json) are untouched.

P1: Serial layer execution (matches DAG engine behavior).
P2: Parallel fan-out via LangGraph super-steps.
P3: Checkpointing via SqliteSaver.
P4: HITL via interrupt().
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, TypedDict, Annotated, Literal

logger = logging.getLogger(__name__)


# ── State schema ──────────────────────────────────────────────────

def _merge_agent_outputs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Reducer: merge agent outputs from parallel nodes."""
    return {**left, **right}


class StudyRoundState(TypedDict, total=False):
    """LangGraph state for one study round execution."""
    # ── inputs (set once) ──
    study_id: str
    round_num: int
    strategy_name: str
    workspace_path: str
    directive_text: str | None
    # ── mutable state ──
    agent_outputs: Annotated[dict[str, Any], _merge_agent_outputs]
    hypothesis: dict | None
    verdict_decision: str | None
    verdict_reason: str | None
    # ── outputs ──
    exec_result: dict | None
    eval_result: dict | None
    aborted: bool
    abort_reason: str | None


# ── Graph construction ────────────────────────────────────────────

def _find_entry_nodes(graph) -> list[str]:
    """Nodes with no incoming edges (multi-entry)."""
    targets = {e.target for e in graph.edges}
    return [n.id for n in graph.nodes if n.enabled and n.id not in targets]


def _find_exit_nodes(graph) -> list[str]:
    """Nodes with no outgoing edges (multi-exit)."""
    sources = {e.source for e in graph.edges}
    return [n.id for n in graph.nodes if n.enabled and n.id not in sources]


def _make_agent_node(
    executor,
    plugin,
    node_config,
    task_text: str,
    workspace: Path,
    agent_ctx: dict[str, Any],
    emit_fn,
    study_id: str,
    round_num: int,
):
    """Create a LangGraph node function for one agent."""
    agent_id = plugin.id

    def agent_node(state: StudyRoundState) -> dict:
        # Collect upstream outputs from state
        upstream: dict[str, str] = {}
        for dep_id, dep_output in (state.get("agent_outputs") or {}).items():
            if isinstance(dep_output, str):
                upstream[dep_id] = dep_output
            elif isinstance(dep_output, dict):
                upstream[dep_id] = json.dumps(dep_output, ensure_ascii=False)
            else:
                upstream[dep_id] = str(dep_output)

        result = executor.execute(
            plugin, task_text, workspace,
            context=agent_ctx,
            upstream_outputs=upstream,
            node=node_config,
        )

        # SSE: agent complete
        if emit_fn:
            emit_fn(study_id, "study_agent_complete", {
                "study_id": study_id,
                "round": round_num,
                "agent": agent_id,
                "status": result.status,
                "elapsed_s": result.elapsed_s,
            })

        # Parse output
        output = result.output
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "agent_outputs": {agent_id: output},
        }

    return agent_node


def build_langgraph(
    graph,
    executor,
    task_text: str,
    workspace: Path,
    agent_ctx: dict[str, Any],
    emit_fn,
    study_id: str,
    round_num: int,
):
    """Convert StudyGraph → compiled LangGraph StateGraph.

    Serial execution: LangGraph runs nodes layer by layer (topological
    super-steps). Within each layer, nodes run in parallel by default,
    but our node functions are idempotent (write to separate files),
    so serial or parallel is safe.

    P1: Serial (topological sort, one node at a time).
    P2: Enable parallel by using add_edge for all edges (LangGraph
    handles parallelism automatically).
    """
    from langgraph.graph import StateGraph, START, END

    g = StateGraph(StudyRoundState)

    # Build plugin map
    registry = getattr(executor, "_registry", None)
    from ..agent.registry import get_default_registry
    reg = registry or get_default_registry()
    node_map = {n.id: n for n in graph.nodes}

    # Add nodes
    for node in graph.nodes:
        if not node.enabled:
            continue
        plugin = reg.get(node.id)
        if plugin is None:
            logger.warning("langgraph: unknown plugin %r, skipping", node.id)
            continue
        node_config = node_map.get(node.id)
        g.add_node(
            node.id,
            _make_agent_node(
                executor, plugin, node_config, task_text,
                workspace, agent_ctx, emit_fn, study_id, round_num,
            ),
        )

    # Add edges
    for edge in graph.edges:
        g.add_edge(edge.source, edge.target)

    # Entry points (START → nodes with no incoming edges)
    entry_nodes = _find_entry_nodes(graph)
    for nid in entry_nodes:
        g.add_edge(START, nid)

    # Exit points (nodes with no outgoing edges → END)
    exit_nodes = _find_exit_nodes(graph)
    for nid in exit_nodes:
        g.add_edge(nid, END)

    return g.compile()


# ── Main entry point ──────────────────────────────────────────────

def run_round_langgraph(
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
    """Execute one round using the LangGraph engine.

    Mirrors ``AutoresearchRunner._run_round_via_dag`` but uses
    LangGraph StateGraph for orchestration.
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

    agent_ctx = {
        "strategy_name": strategy,
        "strategy_dir": run_dir,
        "runs_dir": run_dir,
        "results_tsv": run_dir / "results.tsv",
        "session_id": session,
        "session_manager": runner._session_manager,
    }

    # SSE: round started
    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_init", "status": "started",
    })

    # Build and compile the graph
    compiled = build_langgraph(
        graph, executor, task_text, path,
        agent_ctx, runner._emit, sid, round_num,
    )

    # Initial state
    initial_state: StudyRoundState = {
        "study_id": sid,
        "round_num": round_num,
        "strategy_name": strategy,
        "workspace_path": str(path),
        "directive_text": directive_text,
        "agent_outputs": {},
        "hypothesis": None,
        "verdict_decision": None,
        "verdict_reason": None,
        "exec_result": None,
        "eval_result": None,
        "aborted": False,
        "abort_reason": None,
    }

    # Run the graph
    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_exec", "status": "started",
    })

    result = compiled.invoke(initial_state)

    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_exec", "status": "done",
    })

    # Save agent outputs (mirrors DAG engine)
    agent_outputs = result.get("agent_outputs", {})
    for agent_id, output in agent_outputs.items():
        runner._save_agent_output(run_dir, agent_id, {
            "agent": agent_id,
            "output": json.dumps(output, ensure_ascii=False) if isinstance(output, (dict, list)) else str(output),
            "status": "success",
            "timestamp": time.time(),
        })

    # Rebuild legacy schema (same as DAG engine)
    return runner._rebuild_phase_outputs(agent_outputs, graph)
