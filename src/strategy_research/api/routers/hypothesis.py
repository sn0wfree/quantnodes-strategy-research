"""Hypothesis API router — /api/hypothesis/*"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class HypothesisCreateRequest(BaseModel):
    title: str
    thesis: str = ""
    status: str = "exploring"
    universe: str = ""
    signal_definition: str = ""


class HypothesisUpdateRequest(BaseModel):
    hypothesis_id: str
    status: Optional[str] = None
    title: Optional[str] = None
    thesis: Optional[str] = None
    conclusion: Optional[str] = None


@router.post("/create")
async def hypothesis_create(req: HypothesisCreateRequest, request: Request):
    """创建新 hypothesis。"""
    try:
        from pathlib import Path
        from ...core.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        hyp = registry.create(
            title=req.title,
            thesis=req.thesis,
            status=req.status,
            universe=req.universe,
            signal_definition=req.signal_definition,
        )
        return {"status": "ok", "hypothesis_id": hyp.hypothesis_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def hypothesis_list(
    request: Request,
    status: Optional[str] = None,
    limit: int = 50,
):
    """列出 hypotheses。"""
    try:
        from pathlib import Path
        from ...core.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        items = registry.list()
        if status:
            items = [h for h in items if h.status == status]
        return {
            "status": "ok",
            "hypotheses": [h.to_dict() for h in items[:limit]],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def hypothesis_search(
    request: Request,
    q: str = "",
    limit: int = 20,
):
    """搜索 hypotheses。"""
    try:
        from pathlib import Path
        from ...core.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        results = registry.search(query=q)
        return {
            "status": "ok",
            "results": [h.to_dict() for h in results[:limit]],
            "query": q,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{hypothesis_id}")
async def hypothesis_get(hypothesis_id: str, request: Request):
    """获取单个 hypothesis。"""
    try:
        from pathlib import Path
        from ...core.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        hyp = registry.get(hypothesis_id)
        if hyp is None:
            raise HTTPException(status_code=404, detail=f"Hypothesis {hypothesis_id} not found")
        return {"status": "ok", "hypothesis": hyp.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update")
async def hypothesis_update(req: HypothesisUpdateRequest, request: Request):
    """更新 hypothesis。"""
    try:
        from pathlib import Path
        from ...core.hypothesis import HypothesisRegistry

        hyp_path = getattr(request.app.state, "hypotheses_path", None)
        registry = HypothesisRegistry(path=Path(hyp_path) if hyp_path else None)
        updates = {
            k: v
            for k, v in req.dict().items()
            if v is not None and k != "hypothesis_id"
        }
        hyp = registry.update(req.hypothesis_id, **updates)
        if hyp is None:
            raise HTTPException(status_code=404, detail=f"Hypothesis {req.hypothesis_id} not found")
        return {"status": "ok", "hypothesis": hyp.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
