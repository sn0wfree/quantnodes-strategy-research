"""Goal API router — /api/goal/*"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class GoalStartRequest(BaseModel):
    session_id: str
    objective: str
    risk_tier: str = "research_general"
    market: str = "a_share"
    criteria: Optional[list[str]] = None


class GoalEvidenceRequest(BaseModel):
    session_id: str
    evidence: str
    source: str = "api"
    hypothesis_id: Optional[str] = None


class GoalCompleteRequest(BaseModel):
    session_id: str
    outcome: str = "completed"
    summary: Optional[str] = None


@router.post("/start")
async def goal_start(req: GoalStartRequest, request: Request):
    """创建新 research goal。"""
    try:
        from ...core.goal import GoalStore, GoalStatus, RiskTier
        from ...core.goal.context import default_goal_criteria

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)

        criteria = req.criteria or default_goal_criteria()
        risk_tier = RiskTier(req.risk_tier)

        goal = store.replace_goal(
            session_id=req.session_id,
            objective=req.objective,
            criteria=criteria,
            risk_tier=risk_tier,
        )
        return {"status": "ok", "goal_id": goal.goal_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def goal_status(session_id: str, request: Request):
    """获取当前 goal 状态。"""
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        current = store.get_current_goal(session_id)
        if current is None:
            return {"status": "no_goal", "session_id": session_id}
        return {"status": "ok", "goal": current.__dict__, "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def goal_list(
    request: Request,
    status: Optional[str] = None,
    limit: int = 50,
):
    """列出 goals。"""
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        # GoalStore doesn't have list_goals; return current goal for session
        # For now, return empty list (future: add list_goals method)
        return {"status": "ok", "goals": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evidence")
async def goal_evidence(req: GoalEvidenceRequest, request: Request):
    """添加 evidence。"""
    try:
        from ...core.goal import GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        current = store.get_current_goal(req.session_id)
        if current is None:
            raise HTTPException(status_code=404, detail="No active goal for this session")
        store.add_evidence(
            goal_id=current.goal_id,
            evidence=req.evidence,
            source=req.source,
            hypothesis_id=req.hypothesis_id,
        )
        return {"status": "ok", "goal_id": current.goal_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete")
async def goal_complete(req: GoalCompleteRequest, request: Request):
    """完成 goal。"""
    try:
        from ...core.goal import GoalStore, GoalStatus

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        current = store.get_current_goal(req.session_id)
        if current is None:
            raise HTTPException(status_code=404, detail="No active goal for this session")
        target_status = GoalStatus(req.outcome) if req.outcome in [s.value for s in GoalStatus] else GoalStatus.COMPLETED
        store.transition_status(current.goal_id, target_status)
        return {"status": "ok", "goal_id": current.goal_id, "new_status": target_status.value}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
