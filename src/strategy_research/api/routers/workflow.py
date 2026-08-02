"""Goal Workflow API router — /api/goal/workflow/*"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class WorkflowStartRequest(BaseModel):
    session_id: str
    workflow_name: str
    objective: str


# In-memory store for active workflow runners
_active_runners: dict[str, dict] = {}


@router.post("/start")
async def workflow_start(req: WorkflowStartRequest, request: Request):
    """Start a goal workflow by name."""
    try:
        from ...core.goal.workflow import GoalWorkflowRunner
        from ...core.goal.workflow_config import load_goal_workflow

        config = load_goal_workflow(req.workflow_name)
        runner = GoalWorkflowRunner(
            config=config,
            session_id=req.session_id,
        )

        goal_id = await runner.start(req.objective)

        _active_runners[goal_id] = {
            "runner": runner,
            "session_id": req.session_id,
            "workflow_name": req.workflow_name,
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
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.pause(immediate=immediate)
    return {"status": "ok", "paused": True}


@router.post("/resume")
async def workflow_resume(goal_id: str):
    """Resume a paused workflow."""
    entry = _active_runners.get(goal_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    runner = entry["runner"]
    runner.resume()
    return {"status": "ok", "resumed": True}


@router.get("/list")
async def workflow_list():
    """List available workflow presets."""
    from ...core.goal.workflow_config import list_goal_workflows
    return {"status": "ok", "workflows": list_goal_workflows()}


async def workflow_event_stream(runner):
    """Async generator streaming workflow progress as SSE payloads.

    Shared by the API SSE endpoint (/api/goal/workflow/{goal_id}/events)
    and the WebUI proxy (/workflows/{name}/events). Emits the initial
    progress snapshot, then forwards observer events with heartbeats
    every 1s; terminates on workflow_completed / workflow_failed or a
    terminal progress state.
    """

    import asyncio

    queue: asyncio.Queue = asyncio.Queue()

    class SSEObserver:
        def on_event(self, event: str, data: dict):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    observer = SSEObserver()
    runner.subscribe(observer)

    try:
        # Send initial state
        progress = runner.get_progress()
        yield f"data: {json.dumps({'event': 'progress', 'data': progress})}\n\n"

        # Stream events until workflow completes
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                payload = json.dumps({
                    "event": event,
                    "data": {k: str(v) for k, v in data.items()} if isinstance(data, dict) else str(data),
                })
                yield f"data: {payload}\n\n"

                if event in ("workflow_completed", "workflow_failed"):
                    break
            except asyncio.TimeoutError:
                # Send heartbeat + progress
                progress = runner.get_progress()
                yield f"data: {json.dumps({'event': 'heartbeat', 'data': progress})}\n\n"

                if progress.get("hook_completed") or progress.get("status") in ("completed", "error"):
                    break
    finally:
        runner.unsubscribe(observer)


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


__all__ = ["router"]
