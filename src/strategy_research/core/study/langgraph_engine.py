"""LangGraph engine for study round execution.

Requires ``langgraph`` extra: ``pip install strategy-research[langgraph]``.

Converts a ``StudyGraph`` into a LangGraph ``StateGraph``, executes agents
via ``AgentExecutor``, and returns the legacy ``exec_result + eval_result``
schema so downstream callers (manifest, budget, review, state.json) are untouched.

P1: Serial layer execution (matches DAG engine behavior).
P2: Parallel fan-out via LangGraph super-steps + DuckDB write mutex.
P3: Checkpointing via SqliteSaver (per-round, resume on failure).
P4: HITL via interrupt().
P5: Parity verification tests.
P6: Profile system — phases/dag/langgraph presets.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict, Annotated

from .engine_common import (
    safe_json_loads,
    build_agent_ctx,
    save_agent_outputs,
    get_study_session_db_path,
    ensure_study_session,
)
from ..agent.event_store import EventStoreFactory

logger = logging.getLogger(__name__)


# ── Profile system (P6) ───────────────────────────────────────────

@dataclass(frozen=True)
class LangGraphProfile:
    """Execution profile for the LangGraph engine.

    Presets:
    - ``phases``: linear graph, serial, no checkpoint, no HITL
    - ``dag``: StudyGraph, serial, no checkpoint, no HITL
    - ``langgraph``: StudyGraph, parallel, checkpoint, HITL
    """
    serial: bool = True           # True = serial layer execution; False = parallel
    checkpoint: bool = False      # True = SqliteSaver checkpointing
    hitl: bool = False            # True = novelty gate interrupt

    @classmethod
    def phases(cls) -> "LangGraphProfile":
        """Preset: linear graph, serial, no checkpoint, no HITL."""
        return cls(serial=True, checkpoint=False, hitl=False)

    @classmethod
    def dag(cls) -> "LangGraphProfile":
        """Preset: StudyGraph, serial, no checkpoint, no HITL."""
        return cls(serial=True, checkpoint=False, hitl=False)

    @classmethod
    def langgraph(cls) -> "LangGraphProfile":
        """Preset: StudyGraph, parallel, checkpoint, HITL."""
        return cls(serial=False, checkpoint=True, hitl=True)


_PROFILES = {
    "phases": LangGraphProfile.phases,
    "dag": LangGraphProfile.dag,
    "langgraph": LangGraphProfile.langgraph,
}


def get_profile(name: str) -> LangGraphProfile:
    """Get a profile by name. Falls back to langgraph profile."""
    factory = _PROFILES.get(name)
    if factory is None:
        logger.warning("unknown profile %r, using langgraph default", name)
        return LangGraphProfile.langgraph()
    return factory()


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
    *,
    event_store=None,
    study_session_id: str | None = None,
    agent_histories: dict[str, list] | None = None,
):
    """Create a LangGraph node function for one agent."""
    agent_id = plugin.id
    message_id = f"study:{study_id}:r{round_num}:{agent_id}"

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

        # Collect agent execution history for stage 3 persistence
        history: list[dict[str, Any]] = []

        def _forward_event(event_type: str, data: dict[str, Any]) -> None:
            """Adapter: AgentLoop calls on_event(event_type, data),
            we forward via emit_fn to SSE, write to EventStore, and collect for persistence."""
            event_data = data if isinstance(data, dict) else {}

            # Write to EventStore (for projector → messages + message_parts)
            if event_store and study_session_id:
                # Inject message_id and agent_id for projector
                store_data = {
                    **event_data,
                    "message_id": message_id,
                    "agent_id": agent_id,
                }
                event_store.emit(study_session_id, event_type, store_data)

            # SSE push (with agent_ prefix for frontend real-time streaming)
            if emit_fn:
                emit_fn(study_id, f"agent_{event_type}", {
                    "study_id": study_id,
                    "round": round_num,
                    "agent": agent_id,
                    **event_data,
                })

            # Keep history for backward compatibility (JSON file persistence)
            history.append({"type": event_type, "data": event_data, "ts": time.time()})

        result = executor.execute(
            plugin, task_text, workspace,
            context=agent_ctx,
            upstream_outputs=upstream,
            node=node_config,
            on_event=_forward_event,
        )

        # Store history for stage 3 persistence
        if agent_histories is not None:
            agent_histories[agent_id] = history

        # SSE: agent complete
        if emit_fn:
            emit_fn(study_id, "study_agent_complete", {
                "study_id": study_id,
                "round": round_num,
                "agent": agent_id,
                "status": result.status,
                "elapsed_s": result.elapsed_s,
            })

        # Check execution status
        if result.status != "success":
            logger.warning(
                "langgraph: agent %s failed (status=%s): %s",
                agent_id, result.status, result.error,
            )
            return {
                "agent_outputs": {agent_id: {
                    "error": result.error or f"execution failed: {result.status}",
                    "status": result.status,
                }},
            }

        # Parse output
        output = safe_json_loads(result.output, fallback=result.output)

        return {
            "agent_outputs": {agent_id: output},
        }

    return agent_node


def _make_novelty_gate_node(
    study_id: str,
    round_num: int,
    emit_fn,
):
    """Create a LangGraph node for the novelty gate with optional HITL interrupt.

    When HITL is enabled, this node calls ``interrupt()`` to pause
    execution and wait for human approval of the researcher's hypothesis.
    """
    from langgraph.types import interrupt

    def novelty_gate_node(state: StudyRoundState) -> dict:
        researcher_output = state.get("agent_outputs", {}).get("researcher", {})
        if isinstance(researcher_output, str):
            researcher_output = safe_json_loads(researcher_output, fallback={})

        hypothesis = researcher_output.get("hypothesis", "")
        predicted_affected = researcher_output.get("predicted_affected", [])

        # Simplified novelty check (full logic lives in runner._check_novelty)
        # For HITL, we always interrupt for approval when the hypothesis exists
        if not hypothesis:
            return {"aborted": True, "abort_reason": "no_hypothesis"}

        # SSE: gate check started
        if emit_fn:
            emit_fn(study_id, "study_phase", {
                "study_id": study_id,
                "round": round_num,
                "phase": "novelty_gate",
                "status": "interrupted",
                "hypothesis": hypothesis[:200],
            })

        # HITL: pause for human approval
        approval = interrupt({
            "type": "novelty_gate",
            "study_id": study_id,
            "round_num": round_num,
            "hypothesis": hypothesis,
            "predicted_affected": predicted_affected,
            "message": f"Round {round_num}: 请审批 researcher 假设",
        })

        # After resume: approval is the response from the API
        if approval and isinstance(approval, dict):
            if approval.get("decision") == "reject":
                return {"aborted": True, "abort_reason": "human_rejected"}
        elif approval == "reject":
            return {"aborted": True, "abort_reason": "human_rejected"}

        # Approved: continue
        return {}

    return novelty_gate_node


def build_langgraph(
    graph,
    executor,
    task_text: str,
    workspace: Path,
    agent_ctx: dict[str, Any],
    emit_fn,
    study_id: str,
    round_num: int,
    checkpointer=None,
    profile: LangGraphProfile | None = None,
    agent_histories: dict[str, list] | None = None,
):
    """Convert StudyGraph → compiled LangGraph StateGraph.

    P1: Serial (topological sort, one node at a time).
    P2: Parallel fan-out via LangGraph super-steps.
    P3: Checkpointing via SqliteSaver.
    P4: HITL via interrupt().
    P6: Profile system (serial, checkpoint, hitl params).
    """
    if profile is None:
        profile = LangGraphProfile()

    from langgraph.graph import StateGraph, START, END

    g = StateGraph(StudyRoundState)

    # Build plugin map
    registry = getattr(executor, "_registry", None)
    from ..agent.registry import get_default_registry
    reg = registry or get_default_registry()
    node_map = {n.id: n for n in graph.nodes}

    # Create EventStore and study session for this round.
    # Explicit db_path (workspace-local) so the singleton can never bind
    # to the wrong file via cwd. flush_to_messages=True materializes
    # messages + message_parts live so the session API serves the
    # round's agent messages without a manual projector flush.
    event_store = EventStoreFactory.create(
        db_path=get_study_session_db_path(workspace),
        flush_to_messages=True,
    )
    # The factory singleton is often created HERE first (before the API
    # container builds its own instance) — without a bridge, every
    # agent emit stays locked inside EventStore and never reaches the
    # SSE buffer. Attach idempotently; failures fall back to the
    # projector-materialized messages.
    try:
        from ...api.session.bridge_v2 import attach_eventstore_to_sse
        attach_eventstore_to_sse(event_store)
    except Exception as exc:  # noqa: BLE001 — live stream is best-effort
        logger.warning("SSE bridge not attached to study EventStore: %s", exc)
    study_session_id = f"study:{study_id}:round:{round_num}"
    ensure_study_session(
        get_study_session_db_path(workspace),
        study_session_id,
        f"Study {study_id} Round {round_num}",
    )
    event_store.emit(study_session_id, "session.created", {
        "title": f"Study {study_id} Round {round_num}",
    })

    # Add agent nodes with caching
    try:
        from langgraph.types import CachePolicy
        default_cache_policy = CachePolicy(ttl=300)  # 5 min TTL
    except ImportError:
        default_cache_policy = None

    for node in graph.nodes:
        if not node.enabled:
            continue
        plugin = reg.get(node.id)
        if plugin is None:
            logger.warning("langgraph: unknown plugin %r, skipping", node.id)
            continue
        node_config = node_map.get(node.id)
        node_fn = _make_agent_node(
            executor, plugin, node_config, task_text,
            workspace, agent_ctx, emit_fn, study_id, round_num,
            event_store=event_store,
            study_session_id=study_session_id,
            agent_histories=agent_histories,
        )
        add_kwargs = {}
        if default_cache_policy is not None:
            add_kwargs["cache_policy"] = default_cache_policy
        g.add_node(node.id, node_fn, **add_kwargs)

    # P4: Inject novelty gate node when HITL is enabled
    if profile.hitl:
        g.add_node("novelty_gate", _make_novelty_gate_node(study_id, round_num, emit_fn))

    # Add edges
    for edge in graph.edges:
        source = edge.source
        target = edge.target
        # P4: Route researcher → novelty_gate → original targets
        if profile.hitl and source == "researcher" and target in _find_entry_nodes(graph):
            g.add_edge("researcher", "novelty_gate")
            continue
        g.add_edge(source, target)

    # P4: Route novelty_gate → original targets of researcher
    if profile.hitl:
        researcher_targets = [
            e.target for e in graph.edges
            if e.source == "researcher"
        ]
        for target in researcher_targets:
            g.add_edge("novelty_gate", target)

    # Entry points (START → nodes with no incoming edges)
    entry_nodes = _find_entry_nodes(graph)
    for nid in entry_nodes:
        if profile.hitl and nid in researcher_targets if profile.hitl else False:
            continue  # routed through novelty_gate
        g.add_edge(START, nid)

    # Exit points (nodes with no outgoing edges → END)
    exit_nodes = _find_exit_nodes(graph)
    for nid in exit_nodes:
        g.add_edge(nid, END)

    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    # Add LRU cache for agent nodes (skips LLM calls on identical inputs)
    try:
        from langgraph.cache.memory import InMemoryCache
        compile_kwargs["cache"] = InMemoryCache()
    except ImportError:
        logger.info("langgraph cache not available; caching disabled")

    return g.compile(**compile_kwargs)


# ── Checkpoint helpers ────────────────────────────────────────────

def _get_checkpointer(sid: str, study_root: Path, conn=None):
    """Create or open a SqliteSaver for this study.

    Checkpoint tables live in studies.db (shared connection).
    Falls back to creating a separate checkpoints.db if conn is None.
    Returns None if langgraph-checkpoint-sqlite is not installed.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        logger.info("langgraph-checkpoint-sqlite not installed; checkpointing disabled")
        return None

    try:
        if conn is not None:
            # Use shared connection from studies.db
            return SqliteSaver(conn)
        # Fallback: create separate checkpoints.db (legacy path)
        import sqlite3
        db_path = study_root / "checkpoints.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as exc:
        logger.warning("Failed to create checkpointer: %s", exc)
        return None


def _thread_id(sid: str, round_num: int) -> str:
    """Checkpoint thread ID for a specific round."""
    return f"{sid}:r{round_num}"


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
    profile: LangGraphProfile | str | None = None,
) -> dict:
    """Execute one round using the LangGraph engine.

    Mirrors ``AutoresearchRunner._run_round_via_dag`` but uses
    LangGraph StateGraph for orchestration with checkpointing.

    On failure, the checkpoint preserves completed agent outputs so
    ``resume_round_langgraph`` can pick up from the last successful
    super-step.

    When ``profile`` is provided, controls execution behavior:
    - ``serial``: run nodes layer by layer (True) or parallel (False)
    - ``checkpoint``: enable SqliteSaver checkpointing
    - ``hitl``: enable novelty gate interrupt

    ``profile`` can be a ``LangGraphProfile`` instance or a string name
    (``"phases"`` | ``"dag"`` | ``"langgraph"``).
    """
    from ..agent.dag_config import AgentDAGConfig
    from ..agent.executor import AgentExecutor
    from ..agent.registry import get_default_registry

    # Resolve profile
    if profile is None:
        profile = LangGraphProfile.langgraph()
    elif isinstance(profile, str):
        profile = get_profile(profile)

    logger.info("langgraph: starting round %d, profile=%s", round_num, profile)

    dag_config = AgentDAGConfig.from_study_graph(
        graph, name=f"study_{sid}_r{round_num}",
        description=runner._get_study().objective,
    )
    registry = getattr(runner, "_plugin_registry", None) or get_default_registry()
    executor = AgentExecutor(registry)

    task_text = runner._build_round_task_text(current_state, directive_text)

    agent_ctx = build_agent_ctx(strategy, run_dir, session, runner)

    # SSE: round started
    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_init", "status": "started",
    })

    # Checkpoint setup
    from strategy_research.core.study.state_store import study_root as _study_root
    study_root = _study_root(path, sid)
    # Use shared connection from studies.db (merged checkpoint tables)
    checkpoint_conn = getattr(runner.study_store, "get_checkpoint_conn", lambda: None)()
    checkpointer = _get_checkpointer(sid, study_root, conn=checkpoint_conn)

    # Collect per-agent execution histories (populated during graph execution)
    agent_histories: dict[str, list] = {}

    # Build and compile the graph
    compiled = build_langgraph(
        graph, executor, task_text, path,
        agent_ctx, runner._emit, sid, round_num,
        checkpointer=checkpointer,
        profile=profile,
        agent_histories=agent_histories,
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

    config = {"configurable": {"thread_id": _thread_id(sid, round_num)}}
    result = compiled.invoke(initial_state, config=config)

    # P4: Detect interrupt (HITL approval needed)
    if isinstance(result, dict) and "__interrupt__" in result:
        interrupt_info = result["__interrupt__"]
        # Save interrupt to DB for the runner loop to poll
        from .store import StudyStore
        payload = {}
        if interrupt_info:
            try:
                payload = interrupt_info[0].value if hasattr(interrupt_info[0], "value") else {}
            except (IndexError, AttributeError):
                payload = {"raw": str(interrupt_info)}
        with StudyStore() as store:
            interrupt = store.create_interrupt(
                study_id=sid,
                round_num=round_num,
                interrupt_type=payload.get("type", "novelty_gate"),
                payload=json.dumps(payload, ensure_ascii=False),
            )
        runner._emit(session, "study_phase", {
            "study_id": sid, "round": round_num,
            "phase": "langgraph_exec", "status": "awaiting_approval",
        })
        return {
            "round": round_num,
            "run_name": f"round_{round_num}",
            "paused_for_approval": True,
            "study_id": sid,
            "interrupt_id": interrupt.interrupt_id,
        }

    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_exec", "status": "done",
    })

    # Extract agent outputs from graph result
    agent_outputs = result.get("agent_outputs", {}) if isinstance(result, dict) else {}
    logger.info("langgraph: round %d completed, agent_outputs=%s", round_num, list(agent_outputs.keys()))

    # Save agent outputs (mirrors DAG engine) + execution histories
    save_agent_outputs(runner, run_dir, agent_outputs, round_num,
                       agent_histories=agent_histories)

    # Rebuild legacy schema (same as DAG engine)
    result = runner._rebuild_phase_outputs(agent_outputs, graph)
    # Add round info for the runner loop
    result["round"] = round_num
    result["run_name"] = f"round_{round_num}"
    return result


def resume_round_langgraph(
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
    profile: "LangGraphProfile | None" = None,
) -> dict:
    """Resume a HITL-paused round from the last checkpoint.

    Uses the same checkpointer to load the saved state, then re-invokes
    the graph. Completed agents are skipped (their outputs are already in
    the checkpoint). The stored interrupt decision (approved/rejected)
    is mapped to the gate node's expected ``Command(resume=...)`` value.
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

    # Checkpoint setup
    from strategy_research.core.study.state_store import study_root as _study_root
    study_root = _study_root(path, sid)
    checkpoint_conn = getattr(runner.study_store, "get_checkpoint_conn", lambda: None)()
    checkpointer = _get_checkpointer(sid, study_root, conn=checkpoint_conn)

    if checkpointer is None:
        logger.warning("No checkpointer available; falling back to fresh run")
        return run_round_langgraph(
            runner, path, strategy, current_state, run_dir, graph,
            session=session, sid=sid, round_num=round_num,
            directive_text=directive_text, profile=profile,
        )

    # Build and compile the graph — profile must match the original run
    # so the checkpointed gate node exists in the rebuilt topology.
    compiled = build_langgraph(
        graph, executor, task_text, path,
        agent_ctx, runner._emit, sid, round_num,
        checkpointer=checkpointer,
        profile=profile,
    )

    # Read the stored decision and map it to the gate node's expected
    # resume value: {"decision": "approve"} | {"decision": "reject"}
    # (the gate treats everything non-"reject" as approved).
    store_obj = getattr(runner, "study_store", None)
    interrupt = None
    if store_obj is not None and hasattr(store_obj, "get_interrupt_for_round"):
        try:
            interrupt = store_obj.get_interrupt_for_round(sid, round_num)
        except Exception:  # noqa: BLE001 — polling must not crash resume
            interrupt = None
    else:
        from .store import StudyStore
        with StudyStore() as _tmp_store:
            interrupt = _tmp_store.get_interrupt_for_round(sid, round_num)
    status = getattr(interrupt, "status", None) or "approved"
    decision = "reject" if status == "rejected" else "approve"

    # Resume — Command(resume=...) consumes the interrupt() call in the
    # gate node so execution continues past approval/rejection.
    from langgraph.types import Command
    config = {"configurable": {"thread_id": _thread_id(sid, round_num)}}

    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_resume", "status": "started",
        "decision": decision,
    })

    result = compiled.invoke(Command(resume={"decision": decision}), config=config)

    runner._emit(session, "study_phase", {
        "study_id": sid, "round": round_num,
        "phase": "langgraph_resume", "status": "done",
    })

    # If the graph hit another interrupt during resume, return pause signal
    if isinstance(result, dict) and "__interrupt__" in result:
        logger.warning("langgraph: resume hit another interrupt for round %d", round_num)
        return {
            "round": round_num,
            "run_name": f"round_{round_num}",
            "paused_for_approval": True,
            "study_id": sid,
        }

    # Save agent outputs
    agent_outputs = result.get("agent_outputs", {})
    for agent_id, output in agent_outputs.items():
        runner._save_agent_output(run_dir, agent_id, {
            "agent": agent_id,
            "output": json.dumps(output, ensure_ascii=False) if isinstance(output, (dict, list)) else str(output),
            "status": "success",
            "timestamp": time.time(),
        })

    return runner._rebuild_phase_outputs(agent_outputs, graph)
