"""Strategy API router — ``/api/strategies/*``."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/list")
async def list_strategies(workspace_path: str = Query(...)):
    """List all strategies in the workspace."""
    ws = Path(workspace_path)
    if not ws.exists():
        raise HTTPException(status_code=400, detail=f"workspace not found: {workspace_path}")

    strategies_dir = ws / "strategies"
    if not strategies_dir.exists():
        return {"strategies": []}

    strategies = []
    for d in sorted(strategies_dir.iterdir()):
        if d.is_dir():
            has_strategy_py = (d / "strategy.py").exists()
            has_config_yaml = (d / "config.yaml").exists()
            strategies.append({
                "name": d.name,
                "has_strategy_py": has_strategy_py,
                "has_config_yaml": has_config_yaml,
            })

    return {"strategies": strategies}


@router.get("/check")
async def check_strategy(
    name: str = Query(...),
    workspace_path: str = Query(...),
):
    """Check if a strategy name already exists."""
    ws = Path(workspace_path)
    if not ws.exists():
        raise HTTPException(status_code=400, detail=f"workspace not found: {workspace_path}")

    strategies_dir = ws / "strategies"
    exists = (strategies_dir / name).exists()

    return {"exists": exists, "name": name}
