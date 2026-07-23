"""FastAPI app factory — HTTP API server for strategy research。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI

from .. import __version__


def create_app(
    workspace_path: Optional[Path] = None,
    goal_db_path: Optional[str] = None,
    hypotheses_path: Optional[str] = None,
) -> FastAPI:
    """创建 FastAPI app。"""
    app = FastAPI(
        title="Strategy Research API",
        version=__version__,
        description="HTTP API for quantnodes strategy research framework",
    )

    # Store config in app state
    app.state.workspace_path = workspace_path
    app.state.goal_db_path = goal_db_path
    app.state.hypotheses_path = hypotheses_path

    # Register routers
    from .routers import goal, hypothesis, memory, run, session, validation

    app.include_router(goal.router, prefix="/api/goal", tags=["goal"])
    app.include_router(hypothesis.router, prefix="/api/hypothesis", tags=["hypothesis"])
    app.include_router(validation.router, prefix="/api/validate", tags=["validation"])
    app.include_router(session.router, prefix="/api/session", tags=["session"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(run.router, prefix="/api/run", tags=["run"])

    @app.get("/")
    async def root():
        return {
            "service": "strategy-research-api",
            "version": __version__,
            "docs": "/docs",
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


__all__ = ["create_app"]
