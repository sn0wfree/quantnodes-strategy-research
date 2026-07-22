"""Validation API router — /api/validate/*"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class ValidateRunRequest(BaseModel):
    run_dir: str
    market: str = "a_share"
    monte_carlo: bool = True
    n_simulations: int = 1000
    bootstrap: bool = True
    n_bootstrap: int = 1000
    walk_forward: bool = True
    n_windows: int = 5


@router.post("/run")
async def validate_run(req: ValidateRunRequest, request: Request):
    """执行 validation。"""
    try:
        from ...core.validation.runner import run_validation

        run_dir = Path(req.run_dir)
        if not run_dir.exists():
            raise HTTPException(status_code=404, detail=f"Run dir not found: {run_dir}")

        result = run_validation(
            run_dir=run_dir,
            market=req.market,
            run_monte_carlo=req.monte_carlo,
            n_simulations=req.n_simulations,
            run_bootstrap=req.bootstrap,
            n_bootstrap=req.n_bootstrap,
            run_walk_forward=req.walk_forward,
            n_windows=req.n_windows,
        )
        return {"status": "ok", "validation": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
