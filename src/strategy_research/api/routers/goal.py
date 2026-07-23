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
    criterion_id: Optional[str] = None
    run_id: Optional[str] = None


class GoalCompleteRequest(BaseModel):
    session_id: str
    outcome: str = "complete"
    summary: Optional[str] = None


@router.post("/start")
async def goal_start(req: GoalStartRequest, request: Request):
    """创建新 research goal。"""
    try:
        from ...core.goal import GoalStore, RiskTier
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
        return {
            "status": "ok",
            "goal_id": current.goal_id,
            "goal_status": current.status.value,
            "objective": current.objective,
            "session_id": session_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def goal_list(
    request: Request,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    """列出 goals。"""
    try:
        from ...core.goal import GoalStore, GoalStatus

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)

        status_filter = GoalStatus(status) if status else None
        goals = store.list_goals(
            session_id=session_id,
            status=status_filter,
            limit=limit,
        )
        return {
            "status": "ok",
            "goals": [
                {
                    "goal_id": g.goal_id,
                    "session_id": g.session_id,
                    "goal_status": g.status.value,
                    "objective": g.objective,
                    "created_at": g.created_at,
                }
                for g in goals
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evidence")
async def goal_evidence(req: GoalEvidenceRequest, request: Request):
    """添加 evidence。"""
    try:
        from ...core.goal import EvidenceInput, GoalStore

        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        current = store.get_current_goal(req.session_id)
        if current is None:
            raise HTTPException(status_code=404, detail="No active goal for this session")

        evidence_input = EvidenceInput(
            text=req.evidence,
            source_type=req.source,
            run_id=req.run_id,
            criterion_id=req.criterion_id,
        )
        evidence_record = store.append_evidence(
            session_id=req.session_id,
            goal_id=current.goal_id,
            expected_goal_id=current.goal_id,
            evidence=evidence_input,
        )
        return {
            "status": "ok",
            "goal_id": current.goal_id,
            "evidence_id": evidence_record.evidence_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete")
async def goal_complete(req: GoalCompleteRequest, request: Request):
    """完成 goal。"""
    from ...core.goal import GoalStatus, GoalStore, StaleGoalError

    try:
        db_path = getattr(request.app.state, "goal_db_path", None)
        store = GoalStore(db_path=db_path)
        current = store.get_current_goal(req.session_id)
        if current is None:
            raise HTTPException(status_code=404, detail="No active goal for this session")

        try:
            target_status = GoalStatus(req.outcome)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid outcome: {req.outcome}",
            )

        updated = store.update_status(
            session_id=req.session_id,
            goal_id=current.goal_id,
            expected_goal_id=current.goal_id,
            status=target_status,
            recap=req.summary,
        )
        return {
            "status": "ok",
            "goal_id": updated.goal_id,
            "new_status": updated.status.value,
        }
    except HTTPException:
        raise
    except StaleGoalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
