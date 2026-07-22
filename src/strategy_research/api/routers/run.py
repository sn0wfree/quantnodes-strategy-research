"""Run API router — /api/run/*"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class RunStartRequest(BaseModel):
    workspace_path: str
    strategy_name: str
    action: str = "manual"
    description: str = ""
    timeout: int = 300


@router.post("/start")
async def run_start(req: RunStartRequest, request: Request):
    """启动回测 run。"""
    try:
        from ...core.backtest import run_backtest_script

        workspace = Path(req.workspace_path)
        if not workspace.exists():
            raise HTTPException(status_code=404, detail=f"Workspace not found: {workspace}")

        result = run_backtest_script(
            workspace_path=workspace,
            strategy_name=req.strategy_name,
            action=req.action,
            description=req.description,
            timeout=req.timeout,
        )
        return {"status": "ok" if result["success"] else "error", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def run_status(
    request: Request,
    workspace_path: str = ".",
    strategy_name: str = "",
    run_name: str = "",
):
    """获取 run 状态。"""
    try:
        workspace = Path(workspace_path)
        strategy_dir = workspace / "strategies" / strategy_name
        run_dir = strategy_dir / "runs" / run_name

        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run not found: {run_dir}")

        import json

        metrics_path = run_dir / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path, "r", encoding="utf-8") as f:
                metrics = json.load(f)
            return {"status": "ok", "run": run_name, "metrics": metrics}
        return {"status": "ok", "run": run_name, "metrics": {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def run_list(
    request: Request,
    workspace_path: str = ".",
    strategy_name: str = "",
    limit: int = 20,
):
    """列出 runs。"""
    try:
        workspace = Path(workspace_path)
        strategy_dir = workspace / "strategies" / strategy_name
        runs_dir = strategy_dir / "runs"

        if not runs_dir.exists():
            return {"status": "ok", "runs": []}

        import json

        runs = []
        for d in sorted(runs_dir.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith("run_"):
                metrics_path = d / "metrics.json"
                metrics = {}
                if metrics_path.exists():
                    with open(metrics_path, "r", encoding="utf-8") as f:
                        metrics = json.load(f)
                runs.append({"name": d.name, "metrics": metrics})
                if len(runs) >= limit:
                    break

        return {"status": "ok", "runs": runs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
