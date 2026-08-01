"""Lightweight DI container for the strategy-research API (Phase 3.2).

Before Phase 3.2, the FastAPI app and ``SessionService`` relied on:
- Module-level singletons (``_event_bus = EventBus()``)
- Implicit ordering of ``from X import Y`` (event_bus must be created
  before SessionService)
- Hidden state stored on ``app.state`` ad-hoc

This container makes the wiring explicit:

    container = build_container(
        workspace_path=Path("/ws"),
        db_path=Path("/ws/app.db"),
    )
    app.state.container = container

Routers then resolve services via :mod:`api.dependencies`, which in turn
read from ``app.state.container`` (falling back to direct instantiation
if no container is set, preserving backward compat).

Public API
----------
    AppContainer
        Immutable dataclass holding all long-lived services.

    build_container(*, workspace_path, db_path, ...) -> AppContainer
        Factory that constructs every service with explicit dependencies.

    attach_container(app, container)
        Attach the container to ``app.state`` for FastAPI Depends access.

    get_container(request) -> AppContainer
        FastAPI dependency that returns the active container.

    reset_container(app)
        Detach + clear (for tests).

Why a container (vs. just app.state keys)?
    * Explicit, typed configuration (no stringly-typed keys).
    * One place to construct every service (test seam: swap a fake
      container for the production one).
    * Keeps construction in a single function — easier to reason about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import Request

if TYPE_CHECKING:
    from .session.event_bus_v2 import EventBusV2
    from .session.events import EventBus
    from .session.service import SessionService
    from .session.store import SessionStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppContainer:
    """The bag of long-lived services used by API routers.

    Constructed once at app startup; read-only thereafter. New services
    that need cross-router state should be added here.

    Attributes:
        workspace_path: User's workspace directory (for project files).
        db_path: SQLite database path (sessions / messages).
        event_bus: Legacy EventBus (SSE delivery).
        event_bus_v2: Triple-write EventBusV2.
        session_store: SessionStore bound to db_path.
        session_service: SessionService wired with event_bus_v2.
    """

    workspace_path: Path | None
    db_path: Path
    event_bus: "EventBus"
    event_bus_v2: "EventBusV2"
    session_store: "SessionStore"
    session_service: "SessionService"


# ── Factory ──────────────────────────────────────────────────────────


def build_container(
    *,
    workspace_path: Path | str | None = None,
    db_path: Path | str | None = None,
    event_bus_factory: Callable[[], "EventBus"] | None = None,
    event_bus_v2_factory: Callable[["EventBus", Path], "EventBusV2"] | None = None,
    session_service_factory: Callable[["SessionStore", "EventBus"], "SessionService"] | None = None,
    session_store_factory: Callable[[Path], "SessionStore"] | None = None,
) -> AppContainer:
    """Construct the application container.

    Args:
        workspace_path: Workspace directory (used for project files).
        db_path: SQLite database path. Defaults to ``workspace_path/quantnodes_strategy_research_user.db``
                 if not given.
        *_factory: Optional factory overrides for testing. Each receives
                   the standard constructor args and must return a compatible
                   instance.

    Returns:
        Fully constructed AppContainer with all services wired.

    Note:
        No I/O happens in this function beyond SQLite file creation
        (via SessionStore). Safe to call at app startup.
    """
    from .session.bridge import attach_eventbus_to_sse
    from .session.event_bus_v2 import EventBusV2
    from .session.events import EventBus
    from .session.service import SessionService
    from .session.store import SessionStore

    # Default factories
    if event_bus_factory is None:
        event_bus_factory = EventBus
    if event_bus_v2_factory is None:
        event_bus_v2_factory = EventBusV2
    if session_store_factory is None:
        session_store_factory = SessionStore
    if session_service_factory is None:
        session_service_factory = SessionService

    # Resolve db_path
    if db_path is None:
        ws_path = Path(workspace_path) if workspace_path else Path.home() / ".quantnodes"
        db_path = ws_path / "quantnodes_strategy_research_user.db"
    db_path = Path(db_path)

    # Construct services in dependency order
    event_bus = event_bus_factory()
    # Attach SSE bridge (idempotent — guard via attribute)
    if not getattr(event_bus, "_sse_attached", False):
        attach_eventbus_to_sse(event_bus)
        event_bus._sse_attached = True  # type: ignore[attr-defined]

    event_bus_v2 = event_bus_v2_factory(event_bus, db_path)
    session_store = session_store_factory(db_path)
    session_service = session_service_factory(session_store, event_bus_v2)

    return AppContainer(
        workspace_path=Path(workspace_path) if workspace_path else None,
        db_path=db_path,
        event_bus=event_bus,
        event_bus_v2=event_bus_v2,
        session_store=session_store,
        session_service=session_service,
    )


# ── FastAPI integration ──────────────────────────────────────────────

# Key used on app.state to store the container.
_STATE_KEY_CONTAINER = "_container"


def attach_container(app: Any, container: AppContainer) -> None:
    """Attach a container to ``app.state`` so :func:`get_container` finds it."""
    setattr(app.state, _STATE_KEY_CONTAINER, container)
    logger.info("AppContainer attached to FastAPI app")


def get_container(request: Request) -> AppContainer:
    """FastAPI dependency that returns the active :class:`AppContainer`.

    Raises:
        RuntimeError: if no container is attached. Callers should set one
                      via :func:`attach_container` at app startup.
    """
    container = getattr(request.app.state, _STATE_KEY_CONTAINER, None)
    if container is None:
        raise RuntimeError(
            "AppContainer not attached. Call attach_container(app, build_container(...)) "
            "in your FastAPI lifespan or factory."
        )
    return container


def reset_container(app: Any) -> None:
    """Detach the container (for tests)."""
    if hasattr(app.state, _STATE_KEY_CONTAINER):
        delattr(app.state, _STATE_KEY_CONTAINER)


# ── Adapter for the existing dependencies.py module ────────────────
#
# api/dependencies.py reads from app.state keys (``_event_bus``, etc.).
# After Phase 3.2, it can also pull from app.state._container if one
# is attached. This keeps backward compatibility while allowing the
# container to be the canonical source.


def services_from_container(container: AppContainer) -> dict[str, Any]:
    """Return the dict of services a container provides.

    Useful for bulk-assignment to ``app.state`` so the legacy
    ``api/dependencies`` keys remain populated:

        for key, svc in services_from_container(container).items():
            setattr(app.state, key, svc)
    """
    return {
        "_event_bus": container.event_bus,
        "_event_bus_v2": container.event_bus_v2,
        "_session_service": container.session_service,
    }


__all__ = [
    "AppContainer",
    "attach_container",
    "build_container",
    "get_container",
    "reset_container",
    "services_from_container",
]