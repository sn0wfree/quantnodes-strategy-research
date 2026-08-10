"""Goal Workflow API router — /api/goal/workflow/*"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class WorkflowStartRequest(BaseModel):
    session_id: str
    workflow_name: str
    objective: str


# In-memory store for active workflow runners. Entries are pruned when
# the workflow reaches a terminal state (checked on every access and
# in the SSE generator's finally) and after _ACTIVE_RUNNER_TTL as a
# crash/abandoned-run safety net.
_active_runners: dict[str, dict] = {}
_ACTIVE_RUNNER_TTL = 3600.0  # 1h


def _prune_runners() -> None:
    """Drop terminal + expired runner entries (bounded memory)."""
    now = time.time()
    terminal = []
    for goal_id, entry in list(_active_runners.items()):
        try:
            progress = entry["runner"].get_progress()
        except Exception:
            terminal.append(goal_id)
            continue
        if progress.get("status") in ("completed", "error") or progress.get("hook_completed"):
            terminal.append(goal_id)
        elif now - entry.get("started_at", 0) > _ACTIVE_RUNNER_TTL:
            terminal.append(goal_id)
    for goal_id in terminal:
        _active_runners.pop(goal_id, None)


@router.post("/start")
async def workflow_start(req: WorkflowStartRequest, request: Request):
    """Start a goal workflow by name."""
    try:
        from ...core.goal.workflow import GoalWorkflowRunner
        from ...core.goal.workflow_config import load_goal_workflow
        from .chat import _get_session_service

        config = load_goal_workflow(req.workflow_name)
        session_service = _get_session_service()
        runner = GoalWorkflowRunner(
            config=config,
            session_id=req.session_id,
            session_service=session_service,
        )

        goal_id = await runner.start(req.objective)

        _active_runners[goal_id] = {
            "runner": runner,
            "session_id": req.session_id,
            "workflow_name": req.workflow_name,
            "started_at": time.time(),
        }

        return {
            "status": "ok",
            "goal_id": goal_id,
            "workflow_name": req.workflow_name,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow '{req.workflow_name}' not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def workflow_status(goal_id: str):
    """Get workflow progress for a goal."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    if entry is None:
        return {"status": "not_found", "goal_id": goal_id}

    runner = entry["runner"]
    progress = runner.get_progress()
    return {
        "status": "ok",
        "goal_id": goal_id,
        "workflow_name": entry["workflow_name"],
        "progress": progress,
    }


@router.post("/pause")
async def workflow_pause(goal_id: str, immediate: bool = False):
    """Pause a running workflow."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.pause(immediate=immediate)
    return {"status": "ok", "paused": True}


@router.post("/resume")
async def workflow_resume(goal_id: str):
    """Resume a paused workflow."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.resume()
    return {"status": "ok", "resumed": True}


class WorkflowDirectiveRequest(BaseModel):
    content: str


@router.post("/directive")
async def workflow_directive(goal_id: str, req: WorkflowDirectiveRequest):
    """Add a user directive to a running workflow."""
    _prune_runners()
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.add_directive(req.content)
    return {"status": "ok", "directive_added": True}


@router.get("/list")
async def workflow_list():
    """List available workflow presets."""
    from ...core.goal.workflow_config import list_goal_workflows
    return {"status": "ok", "workflows": list_goal_workflows()}


@router.get("/{name}/graph")
async def workflow_graph(name: str):
    """Return the DAG structure (nodes + edges) for a workflow preset."""
    from ...core.goal.workflow_config import load_goal_workflow
    try:
        config = load_goal_workflow(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    nodes = [{"id": a.id, "label": a.id} for a in config.agents]
    edges = [
        {"source": dep, "target": agent.id}
        for agent in config.agents
        for dep in config.dag.get(agent.id, [])
    ]
    return {
        "status": "ok",
        "name": config.name,
        "description": config.description,
        "nodes": nodes,
        "edges": edges,
    }


async def workflow_event_stream(runner):
    """Async generator streaming workflow progress as SSE payloads.

    Shared by the API SSE endpoint (/api/goal/workflow/{goal_id}/events)
    and the WebUI proxy (/workflows/{name}/events). Emits the initial
    progress snapshot, then forwards observer events with heartbeats
    every 1s; terminates on workflow_completed / workflow_failed or a
    terminal progress state.

    SSE frame conventions (frontend EVENT_TYPES contract):
      - ``event: progress`` + data: ``get_progress()`` dict (initial snapshot,
        heartbeat) — frontend updates executionProgress + agent statuses.
      - ``event: dag_update`` + data: ``{node_id, status}`` — emitted for each
        ``agent_complete`` observer event so the DAG panel can update
        individual nodes in real time without waiting for the next heartbeat.
    """
    import asyncio

    queue: asyncio.Queue = asyncio.Queue()

    def _sse(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    class SSEObserver:
        def on_event(self, event: str, data: dict):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    observer = SSEObserver()
    runner.subscribe(observer)

    try:
        yield _sse("progress", runner.get_progress())

        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                for frame in _handle_observer_event(event, data, runner):
                    yield frame
                if event in ("workflow_completed", "workflow_failed"):
                    break
            except asyncio.TimeoutError:
                yield _sse("progress", runner.get_progress())

                if _is_terminal(runner):
                    break
    finally:
        runner.unsubscribe(observer)
        # Terminal (or abandoned) run: drop the runner entry so
        # _active_runners cannot grow unbounded.
        _prune_runners()


def _is_terminal(runner) -> bool:
    """Whether the runner has reached a terminal progress state."""
    progress = runner.get_progress()
    return (
        progress.get("hook_completed")
        or progress.get("status") in ("completed", "error")
    )


def _handle_observer_event(event: str, data: dict, runner) -> list[str]:
    """Translate a runner observer event into one or more SSE frames."""
    frames: list[str] = []

    def _sse(name: str, payload: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    if event == "agent_complete" and isinstance(data, dict):
        agent_id = data.get("agent_id")
        if agent_id:
            frames.append(_sse("dag_update", {
                "node_id": agent_id,
                "status": "success",
            }))
    elif event == "layer_start":
        # Surface layer_start as a progress refresh so the frontend
        # sees current_layer moving forward.
        frames.append(_sse("progress", runner.get_progress()))
    elif event in ("workflow_completed", "workflow_failed"):
        frames.append(_sse("progress", runner.get_progress()))
        frames.append(_sse(event, data))
    return frames


@router.get("/events")
async def workflow_events(goal_id: str):
    """SSE endpoint for workflow progress events."""
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return StreamingResponse(
        workflow_event_stream(entry["runner"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Modular DAG workflows (docs/workflow-module-design.md, Commit 3)
# Endpoints: definitions CRUD / start-definition / approve / run status
# ─────────────────────────────────────────────────────────────────────

from pathlib import Path as _Path  # noqa: E402

_run_registry = None
_run_store = None


def _definition_workspace() -> _Path:
    import os
    return _Path(os.environ.get("SR_WORKSPACE_PATH", str(_Path.cwd())))


def _get_run_store() -> Any:
    global _run_store
    if _run_store is None:
        from ...core.workflow.store import WorkflowStore
        _run_store = WorkflowStore(db_path=_definition_workspace() / "workflows.db")
    return _run_store


def _get_run_registry() -> Any:
    global _run_registry
    if _run_registry is None:
        from ...core.workflow.executor import WorkflowRunRegistry
        _run_registry = WorkflowRunRegistry()
    return _run_registry


class WorkflowDefinitionPayload(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    budget: dict = {}
    llm: dict = {}
    params: dict = {}
    nodes: list[dict] = []
    edges: list[dict] = []


class WorkflowStartDefinitionRequest(BaseModel):
    session_id: str
    definition_name: str
    objective: str
    params: dict = {}


class WorkflowApproveRequest(BaseModel):
    run_id: str
    approved: bool
    edits: dict | None = None


@router.post("/definitions")
async def definitions_create(payload: WorkflowDefinitionPayload, request: Request):
    """Create or overwrite a user workflow definition."""
    from ...core.workflow.builtin import save_user_definition
    from ...core.workflow.definition import WorkflowDefinition

    definition = WorkflowDefinition.from_dict(payload.model_dump(), source="user")
    errors = definition.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    path = save_user_definition(definition, _definition_workspace())
    return {"status": "ok", "name": definition.name, "path": str(path),
            "nodes": len(definition.nodes), "edges": len(definition.edges)}


@router.get("/definitions")
async def definitions_list(request: Request):
    """List all definitions (user shadows builtin, source marked)."""
    from ...core.workflow.builtin import list_definitions
    return {"status": "ok", "definitions": list_definitions(_definition_workspace())}


@router.get("/definitions/{name}")
async def definitions_get(name: str, request: Request):
    """Fetch a definition by name."""
    from ...core.workflow.builtin import load_definition
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    return {"status": "ok", "definition": definition.to_dict()}


@router.delete("/definitions/{name}")
async def definitions_delete(name: str, request: Request):
    """Delete a user definition. Builtin definitions are read-only."""
    from ...core.workflow.builtin import delete_user_definition, load_definition
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    if definition.source == "builtin":
        raise HTTPException(status_code=422, detail="Builtin definitions are read-only; use /definitions/{name}/copy first")
    if delete_user_definition(name, _definition_workspace()):
        return {"status": "ok", "deleted": name}
    raise HTTPException(status_code=500, detail="Failed to delete definition")


@router.post("/definitions/{name}/copy")
async def definitions_copy(name: str, request: Request):
    """Copy a definition (typically a builtin) into the user directory."""
    import json as _json
    from ...core.workflow.builtin import load_definition, save_user_definition
    from ...core.workflow.definition import WorkflowDefinition

    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    user_copy = WorkflowDefinition.from_dict(
        _json.loads(definition.to_json()), source="user",
    )
    path = save_user_definition(user_copy, _definition_workspace())
    return {"status": "ok", "name": user_copy.name, "path": str(path)}


@router.get("/definitions/{name}/graph")
async def definitions_graph(name: str, request: Request):
    """Return nodes + edges for the WorkflowDAG frontend (typed nodes)."""
    from ...core.workflow.builtin import load_definition
    definition = load_definition(name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{name}' not found")
    nodes = [{"id": n.id, "label": n.label or n.id, "type": n.type} for n in definition.nodes]
    edges = [{"source": e.source, "target": e.target} for e in definition.edges]
    return {"status": "ok", "name": definition.name,
            "description": definition.description, "nodes": nodes, "edges": edges}


@router.post("/start-definition")
async def start_definition(req: WorkflowStartDefinitionRequest, request: Request):
    """Start a modular workflow definition run."""
    from ...core.workflow.builtin import load_definition
    from ...core.workflow.executor import WorkflowRunner
    from ...core.workflow.node_types import register_builtin_tool_executors

    definition = load_definition(req.definition_name, _definition_workspace())
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Definition '{req.definition_name}' not found")

    store = _get_run_store()
    registry = _get_run_registry()
    register_builtin_tool_executors()
    runner = WorkflowRunner(
        definition=definition,
        workspace=_definition_workspace(),
        objective=req.objective,
        store=store,
        session_id=req.session_id,
        params_override=req.params or None,
    )
    run_id = runner.start()
    registry.put(runner)
    snapshot = runner.status_snapshot()
    return {"status": "ok", "run_id": run_id, "run": snapshot}


@router.post("/approve")
async def workflow_approve(req: WorkflowApproveRequest, request: Request):
    """Respond to a pending approval gate (approve or reject + edits)."""
    registry = _get_run_registry()
    runner = registry.get(req.run_id)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"Run '{req.run_id}' not active")
    if not runner.approve(req.approved, req.edits):
        raise HTTPException(status_code=409, detail="Run is not awaiting approval")
    return {"status": "ok", "run_id": req.run_id, "run": runner.status_snapshot()}


@router.get("/run/{run_id}/status")
async def run_status(run_id: str):
    """Run status: live snapshot if active, else persisted record."""
    store = _get_run_store()
    registry = _get_run_registry()
    runner = registry.get(run_id)
    if runner is not None:
        return {"status": "ok", "run_id": run_id, "run": runner.status_snapshot()}
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {"status": "ok", "run_id": run_id, "run": record}


@router.get("/run/{run_id}")
async def run_detail(run_id: str):
    """Run detail: lifecycle + segments + node outputs + approvals."""
    store = _get_run_store()
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {
        "status": "ok",
        "run": record,
        "segments": store.list_segments(run_id),
        "node_outputs": store.list_node_outputs(run_id),
        "approvals": [
            store.get_approval(run_id, node_id) or {"run_id": run_id, "node_id": node_id}
            for node_id in _approval_node_ids(store, run_id)
        ],
    }


def _approval_node_ids(store: Any, run_id: str) -> list[str]:
    import json
    rows = store._ensure_conn().execute(
        "SELECT node_id FROM approvals WHERE run_id = ? ORDER BY created_at", (run_id,),
    ).fetchall()
    return [row["node_id"] for row in rows]


@router.get("/run/{run_id}/events")
async def run_events(run_id: str, request: Request):
    """SSE event history (polling-based, works after process restart)."""
    store = _get_run_store()

    async def _stream():
        import asyncio as _asyncio
        last_seq = 0
        while True:
            events = store.list_events(run_id, limit=500)
            for event in reversed(events):
                if event["seq"] <= last_seq:
                    continue
                last_seq = event["seq"]
                yield f"event: {event['event_type']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
            record = store.get_run(run_id)
            if record and record["status"] in ("completed", "failed", "cancelled"):
                yield f"event: run_terminal\ndata: {json.dumps({'status': record['status']}, ensure_ascii=False)}\n\n"
                return
            try:
                await _asyncio.wait_for(_asyncio.Future(), timeout=1.0)
            except _asyncio.TimeoutError:
                continue

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@router.delete("/run/{run_id}")
async def run_delete(run_id: str):
    """Delete run history (all related rows)."""
    store = _get_run_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    registry = _get_run_registry()
    registry.pop(run_id)
    store.delete_run(run_id)
    return {"status": "ok", "deleted": run_id}


__all__ = ["router"]
