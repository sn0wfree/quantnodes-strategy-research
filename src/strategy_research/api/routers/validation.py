"""Validation API router — /api/validate/*"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


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

        # Security: resolve run_dir and require it to live under the
        # configured workspace (env: STRATEGY_RESEARCH_VALIDATE_ROOT,
        # default $HOME). Prevents arbitrary FS read by this endpoint.
        try:
            run_dir = Path(req.run_dir).resolve()
            root = Path(
                os.environ.get("STRATEGY_RESEARCH_VALIDATE_ROOT", os.path.expanduser("~"))
            ).resolve()
            run_dir.relative_to(root)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"run_dir must resolve under {root}",
            )
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
