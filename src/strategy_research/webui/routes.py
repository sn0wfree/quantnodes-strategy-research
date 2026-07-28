"""Web UI routes — FastAPI page routes with Jinja2 + HTMX。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/webui")

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _get_workspace(request: Request) -> Optional[Path]:
    ws = getattr(request.app.state, "workspace_path", None)
    return Path(ws) if ws else None


def _count_strategies(workspace: Path) -> int:
    strategies_dir = workspace / "strategies"
    if not strategies_dir.exists():
        return 0
    return sum(1 for d in strategies_dir.iterdir() if d.is_dir() and (d / "strategy.py").exists())


def _get_recent_runs(workspace: Path, limit: int = 5):
    strategies_dir = workspace / "strategies"
    if not strategies_dir.exists():
        return []
    runs = []
    for d in sorted(strategies_dir.iterdir()):
        if not d.is_dir() or not (d / "strategy.py").exists():
            continue
        runs_dir = d / "runs"
        if not runs_dir.exists():
            continue
        for r in sorted(runs_dir.iterdir(), reverse=True):
            if r.is_dir() and r.name.startswith("run_"):
                metrics_path = r / "metrics.json"
                metrics = {}
                if metrics_path.exists():
                    with open(metrics_path) as f:
                        metrics = json.load(f)
                runs.append({"name": r.name, "strategy": d.name, "metrics": metrics})
                if len(runs) >= limit:
                    return runs
    return runs


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard page."""
    workspace = _get_workspace(request)

    stats = {"goals": 0, "hypotheses": 0, "runs": 0, "strategies": 0}
    recent_runs = []
    recent_goals = []

    if workspace and workspace.exists():
        stats["strategies"] = _count_strategies(workspace)
        recent_runs = _get_recent_runs(workspace, limit=5)

        # Count runs
        strategies_dir = workspace / "strategies"
        if strategies_dir.exists():
            for d in strategies_dir.iterdir():
                if d.is_dir():
                    runs_dir = d / "runs"
                    if runs_dir.exists():
                        stats["runs"] += sum(1 for r in runs_dir.iterdir() if r.is_dir() and r.name.startswith("run_"))

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "recent_runs": recent_runs,
        "recent_goals": recent_goals,
    })


@router.get("/goals", response_class=HTMLResponse)
async def goals_list(request: Request):
    """Goals list page."""
    goals = []
    try:
        from ..core.goal import GoalStore
        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        goal_records = store.list_goals(limit=100)
        goals = [
            {
                "goal_id": g.goal_id,
                "session_id": g.session_id,
                "status": g.status.value,
                "objective": g.objective,
                "ui_summary": g.ui_summary,
                "risk_tier": g.risk_tier.value,
                "created_at": g.created_at,
                "updated_at": g.updated_at,
            }
            for g in goal_records
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to load goals: %s", e)

    return templates.TemplateResponse("goals/list.html", {
        "request": request,
        "goals": goals,
    })


@router.get("/hypotheses", response_class=HTMLResponse)
async def hypotheses_list(request: Request):
    """Hypotheses list page."""
    hypotheses = []
    try:
        from pathlib import Path

        from ..core.hypothesis import HypothesisRegistry
        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        hypotheses = [h.__dict__ for h in registry.list()]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to load hypotheses: %s", e)

    return templates.TemplateResponse("hypotheses/list.html", {
        "request": request,
        "hypotheses": hypotheses,
    })


@router.get("/runs", response_class=HTMLResponse)
async def runs_list(request: Request):
    """Runs list page."""
    workspace = _get_workspace(request)
    runs_by_strategy = {}

    if workspace and workspace.exists():
        strategies_dir = workspace / "strategies"
        if strategies_dir.exists():
            for d in sorted(strategies_dir.iterdir()):
                if not d.is_dir() or not (d / "strategy.py").exists():
                    continue
                runs_dir = d / "runs"
                if not runs_dir.exists():
                    continue
                runs = []
                for r in sorted(runs_dir.iterdir(), reverse=True):
                    if r.is_dir() and r.name.startswith("run_"):
                        metrics_path = r / "metrics.json"
                        metrics = {}
                        if metrics_path.exists():
                            with open(metrics_path) as f:
                                metrics = json.load(f)
                        runs.append({"name": r.name, "metrics": metrics})
                runs_by_strategy[d.name] = runs

    return templates.TemplateResponse("runs/list.html", {
        "request": request,
        "runs_by_strategy": runs_by_strategy,
    })


@router.get("/memory", response_class=HTMLResponse)
async def memory_search(request: Request):
    """Memory search page."""
    return templates.TemplateResponse("memory/search.html", {
        "request": request,
        "results": [],
    })


@router.get("/memory/search", response_class=HTMLResponse)
async def memory_search_htmx(request: Request, q: str = ""):
    """HTMX endpoint for memory search."""
    results = []
    if q.strip():
        try:
            from ..core.memory import MemoryFTS5
            mem = MemoryFTS5()
            results = mem.search(query=q, max_results=20)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Memory search failed: %s", e)

    # Return just the table HTML for HTMX
    if results:
        rows = ""
        for item in results:
            rows += f"""<tr>
                <td>{item.get("title", "")}</td>
                <td>{item.get("path", "")}</td>
                <td>{item.get("description", "")[:80]}</td>
                <td>{item.get("score", 0):.2f}</td>
            </tr>"""
        html = f"""<table>
            <thead><tr><th>Title</th><th>Path</th><th>Description</th><th>Score</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>"""
    else:
        html = '<div class="empty">No results. Try a search query.</div>'

    return HTMLResponse(content=html)


# ── Workflow pages ──────────────────────────────────────────────


@router.get("/workflows", response_class=HTMLResponse)
async def workflows_list(request: Request):
    """Workflows list page — lists available workflow presets."""
    from ..core.goal.workflow_config import list_goal_workflows
    workflows = list_goal_workflows()
    return templates.TemplateResponse("goals/workflows.html", {
        "request": request,
        "workflows": workflows,
    })


@router.get("/workflows/{workflow_name}", response_class=HTMLResponse)
async def workflow_detail(request: Request, workflow_name: str):
    """Workflow detail page — shows DAG + start form."""
    from ..core.goal.workflow_config import load_goal_workflow
    try:
        config = load_goal_workflow(workflow_name)
    except FileNotFoundError:
        return HTMLResponse(content=f"<h1>Workflow '{workflow_name}' not found</h1>", status_code=404)

    dag_data = json.dumps(config.dag)
    agents = [{"id": a.id, "prompt_file": a.prompt_file} for a in config.agents]

    return templates.TemplateResponse("goals/workflow_detail.html", {
        "request": request,
        "workflow_name": workflow_name,
        "description": config.description,
        "dag_data": dag_data,
        "agents": agents,
    })


@router.get("/workflows/{workflow_name}/events")
async def workflow_events_sse(request: Request, workflow_name: str, goal_id: str):
    """SSE endpoint for workflow progress (proxies to API)."""
    from ..api.routers.workflow import _active_runners
    import asyncio
    import json

    entry = _active_runners.get(goal_id)
    if entry is None:
        return HTMLResponse(content="Workflow not found", status_code=404)

    runner = entry["runner"]

    async def event_generator():
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
            progress = runner.get_progress()
            yield f"data: {json.dumps({'event': 'progress', 'data': progress})}\n\n"

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
                    progress = runner.get_progress()
                    yield f"data: {json.dumps({'event': 'heartbeat', 'data': progress})}\n\n"
                    if progress.get("hook_completed") or progress.get("status") in ("completed", "error"):
                        break
        finally:
            runner.unsubscribe(observer)

    from starlette.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


__all__ = ["router"]
