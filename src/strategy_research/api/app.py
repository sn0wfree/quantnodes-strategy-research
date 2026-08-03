"""FastAPI app factory — HTTP API server for strategy research。"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
    # 配置应用日志级别 - 确保 info 级别日志可见
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 设置 strategy_research 命名空间日志级别
    logging.getLogger("strategy_research").setLevel(logging.INFO)
    logger.info("[STARTUP] create_app called")

    # Register the core loop's compaction persister (legacy fallback
    # path) so core/agent/loop.py never imports the api layer.
    from ..core.agent.loop import register_compaction_persister
    from .routers.web_session import persist_message
    register_compaction_persister(persist_message)
    # Resolve from environment if not explicitly provided (supports uvicorn --reload factory mode)
    if workspace_path is None:
        env = os.environ.get("SR_WORKSPACE_PATH")
        workspace_path = Path(env) if env else None
    if goal_db_path is None:
        goal_db_path = os.environ.get("SR_GOAL_DB_PATH")
    if hypotheses_path is None:
        hypotheses_path = os.environ.get("SR_HYPOTHESES_PATH")
    if static_dir is None:
        static_dir = os.environ.get("STATIC_DIR")
    if cors_origins is None:
        cors_str = os.environ.get("CORS_ORIGINS")
        cors_origins = cors_str.split(",") if cors_str else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Background tasks that run during the app's lifetime.

        - Set EventStore for event publishing (via sse_pusher callback)
        - Schedule model catalog refresh 5s after startup so the user
          sees fresh metadata without blocking first response.
        """
        # EventStore uses sse_pusher callback — no set_loop needed
        from .routers.chat import _event_store
        logger.info("[STARTUP] EventStore ready for event publishing")

        task = asyncio.create_task(_refresh_model_catalog_async())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app = FastAPI(
        title="Strategy Research API",
        version=__version__,
        description="HTTP API for quantnodes strategy research framework",
        lifespan=lifespan,
    )

    # Store config in app state
    app.state.workspace_path = workspace_path
    app.state.goal_db_path = goal_db_path
    app.state.hypotheses_path = hypotheses_path

    # Initialize DuckDB on startup
    if workspace_path:
        try:
            from ..core.db import init_db
            init_db(workspace_path)
            logger.info("DuckDB initialized at %s", workspace_path)
        except Exception as exc:
            logger.warning("DuckDB init failed: %s", exc)

        # Smart scaffold: recursively ensure workspace templates/ mirrors
        # package templates/. Idempotent — user customizations preserved.
        # See core/workspace_setup.py and docs/scaffold-fix.md.
        try:
            from ..core.workspace_setup import smart_init_workspace_templates
            smart_init_workspace_templates(workspace_path)
        except Exception as exc:
            logger.warning("Smart scaffold failed: %s", exc)

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
    from .routers import admin, auth, chat, goal, hypothesis, memory, run, session, validation, web_session, workflow

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(web_session.router, prefix="/api/chat/session", tags=["chat-session"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

    app.include_router(goal.router, prefix="/api/goal", tags=["goal"])
    app.include_router(workflow.router, prefix="/api/goal/workflow", tags=["workflow"])
    app.include_router(hypothesis.router, prefix="/api/hypothesis", tags=["hypothesis"])
    app.include_router(validation.router, prefix="/api/validate", tags=["validation"])
    app.include_router(session.router, prefix="/api/session", tags=["session"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(run.router, prefix="/api/run", tags=["run"])

    # System info (settings modal)
    from .routers import system
    app.include_router(system.router, prefix="/api/system", tags=["system"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # WebUI routes (mounted inside factory so they survive --reload)
    from ..webui.routes import router as webui_router
    app.include_router(webui_router, tags=["webui"])

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

            # Resolve + contain the path inside the static dir (blocks
            # path traversal via ".." / encoded segments).
            try:
                resolved = (static_path / full_path).resolve()
                resolved.relative_to(static_path.resolve())
            except (ValueError, OSError):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not found")

            if resolved.is_file():
                return FileResponse(resolved)
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

    return app


def configure_from_env():
    """Read environment variables and return configuration."""
    config = {
        "cors_origins": os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else None,
        "static_dir": os.environ.get("STATIC_DIR"),
        "jwt_secret": os.environ.get("JWT_SECRET"),
    }
    return config


async def _refresh_model_catalog_async() -> None:
    """Background: refresh model catalog for the currently configured LLM.

    Runs 5s after startup so the first user request doesn't pay the
    network cost. Failure is silent (log warning; bundled data is used).

    User-config overrides on ``model_context_tokens`` etc. are passed
    through so the refreshed entry reflects user expectations.
    """
    await asyncio.sleep(5)
    try:
        from ..core.llm.config import LLMConfig
        from ..core.llm.model_catalog import refresh_model_info

        llm_config = LLMConfig.load()
        provider = llm_config.provider
        model = llm_config.model
        if not provider or not model or provider == "auto":
            logger.debug("Model catalog refresh skipped: LLM not configured")
            return
        info = await refresh_model_info(
            provider, model, user_config=llm_config
        )
        logger.info(
            "Model catalog refreshed: %s/%s context=%d source=%s",
            info.provider,
            info.model,
            info.context_tokens,
            info.source,
        )
    except Exception as exc:
        logger.warning("Model catalog refresh failed: %s", exc)


__all__ = ["create_app", "configure_from_env", "_refresh_model_catalog_async"]
