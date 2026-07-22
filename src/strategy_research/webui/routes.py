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


__all__ = ["router"]