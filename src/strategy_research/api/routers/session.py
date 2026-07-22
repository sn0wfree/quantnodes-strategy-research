"""Session API router — /api/session/*"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class SessionStartRequest(BaseModel):
    workspace_path: str
    agent_id: str = "api_user"
    user_id: str = "api_user"


@router.post("/start")
async def session_start(req: SessionStartRequest, request: Request):
    """启动新 session。"""
    try:
        from ...core.session import SessionDB, SessionManager

        db = SessionDB()
        mgr = SessionManager(db=db)
        session = mgr.start_session(agent_id=req.agent_id, user_id=req.user_id)
        return {"status": "ok", "session_id": session.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def session_list(
    request: Request,
    workspace_path: str = ".",
    limit: int = 20,
):
    """列出 sessions。"""
    try:
        from ...core.session import SessionDB, SessionManager

        db = SessionDB()
        mgr = SessionManager(db=db)
        sessions = mgr.list_sessions(limit=limit)
        return {"status": "ok", "sessions": [s.__dict__ for s in sessions]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
