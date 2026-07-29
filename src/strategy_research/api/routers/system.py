"""System info API — workspace status, LLM config, user count."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


@router.get("/info")
async def system_info():
    """Return system information for the settings modal."""
    from strategy_research.api.user_db import get_user_db
    from strategy_research.cli.llm_config_check import check_llm_config

    db = get_user_db()

    # Workspace path
    workspace = os.environ.get("SR_WORKSPACE_PATH", str(Path.cwd()))

    # LLM status
    try:
        llm = check_llm_config()
    except Exception:
        llm = {"configured": False, "provider": "unknown", "model": "unknown", "api_key_source": "unknown"}

    return {
        "workspace_path": workspace,
        "user_count": db.user_count(),
        "llm": llm,
    }
