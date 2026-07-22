"""Memory API router — /api/memory/*"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/search")
async def memory_search(
    request: Request,
    q: str = "",
    limit: int = 10,
):
    """搜索记忆。"""
    try:
        from ...core.memory import MemoryFTS5

        mem = MemoryFTS5()
        results = mem.search(query=q, max_results=limit)
        return {"status": "ok", "results": results, "query": q}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
