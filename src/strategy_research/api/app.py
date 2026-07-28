"""FastAPI app factory — HTTP API server for strategy research。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.cors import CORSMiddleware

from .. import __version__

logger = logging.getLogger(__name__)


def create_app(
    workspace_path: Optional[Path] = None,
    goal_db_path: Optional[str] = None,
    hypotheses_path: Optional[str] = None,
    static_dir: Optional[str] = None,
    cors_origins: Optional[list] = None,
) -> FastAPI:
    """创建 FastAPI app。

    Args:
        workspace_path: Workspace directory path.
        goal_db_path: Goal DB path.
        hypotheses_path: Hypotheses file path.
        static_dir: Static files directory (e.g. `webui/static/`). If exists, will be served at `/`.
        cors_origins: CORS allowed origins (default: `["*"]` for dev).
    """
    app = FastAPI(
        title="Strategy Research API",
        version=__version__,
        description="HTTP API for quantnodes strategy research framework",
    )

    # Store config in app state
    app.state.workspace_path = workspace_path
    app.state.goal_db_path = goal_db_path
    app.state.hypotheses_path = hypotheses_path

    # CORS
    origins = cors_origins if cors_origins is not None else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register auth middleware
    from .middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware)

    # Register routers
    from .routers import auth, chat, goal, hypothesis, memory, run, session, validation, web_session, workflow

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(web_session.router, prefix="/api/chat/session", tags=["chat-session"])

    app.include_router(goal.router, prefix="/api/goal", tags=["goal"])
    app.include_router(workflow.router, prefix="/api/goal/workflow", tags=["workflow"])
    app.include_router(hypothesis.router, prefix="/api/hypothesis", tags=["hypothesis"])
    app.include_router(validation.router, prefix="/api/validate", tags=["validation"])
    app.include_router(session.router, prefix="/api/session", tags=["session"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(run.router, prefix="/api/run", tags=["run"])

    # Serve static files if available
    static_path = Path(static_dir) if static_dir else Path(__file__).parent.parent.parent.parent / "webui" / "static"
    if static_path.exists():
        # Mount assets directory
        assets_path = static_path / "assets"
        if assets_path.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

        # Serve index.html for all non-API routes (SPA fallback)
        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(static_path / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            # Don't intercept API routes
            if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not found")

            # Check if file exists in static dir
            file_path = static_path / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            # Fallback to index.html (SPA routing)
            return FileResponse(static_path / "index.html")

        logger.info("Serving static files from %s", static_path)
    else:
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


def configure_from_env():
    """Read environment variables and return configuration."""
    config = {
        "cors_origins": os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else None,
        "static_dir": os.environ.get("STATIC_DIR"),
        "jwt_secret": os.environ.get("JWT_SECRET"),
    }
    return config


__all__ = ["create_app", "configure_from_env"]