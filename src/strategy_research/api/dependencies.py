"""FastAPI dependency providers (Phase 3.1).

TODO(architecture): planned-but-unwired. docs/architecture-review.md
§3.1 claims "✅ FastAPI Depends providers", but zero routers use
``Depends(...)`` from this module today — they call the private
``routers/chat.py::_get_session_service`` factory instead. This module
is exercised only by tests. To activate: swap router dependencies to
these providers (``get_session_service``, ``get_event_bus_v2``, …) and
remove the private factories. Note: ``get_event_bus_v2``'s per-instance
``_sse_attached`` guard differs from the module-level bridge flag in
``api/session/bridge.py`` — wiring both paths without care can leave a
second EventBus marked "attached" but never bridged to sse_buffer
(silent SSE loss).

Before Phase 3.1, every router that needed a shared service called a
private module-level ``_get_session_service()`` factory. This had two
problems:

1. Each router constructed its own ``SessionService`` / ``EventBusV2``
   on first call (or worse, hard-coded module-level singletons).
2. Tests could not easily substitute a fake service without
   monkey-patching internals.

This module provides FastAPI ``Depends`` callables that:
1. Lazily construct services on first request (not at import time).
2. Cache the instance on ``app.state`` so all routers share it.
3. Allow tests to override via ``app.dependency_overrides``.

Public API
----------
    get_event_bus(request)
        Returns the singleton EventBus (legacy + V2 wrapper).

    get_session_service(request)
        Returns the singleton SessionService.

    get_db_path(request)
        Returns the SQLite DB path.

    get_event_bus_v2(request)
        Returns the EventBusV2 wrapper.

Usage in routers::

    from ..dependencies import get_session_service

    @router.post("/foo")
    async def foo(svc: SessionService = Depends(get_session_service)):
        ...

Test override::

    app.dependency_overrides[get_session_service] = lambda: fake_service
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from .session.event_bus_v2 import EventBusV2
    from .session.events import EventBus
    from .session.service import SessionService
    from .session.store import SessionStore


# ── Cache keys on app.state ──────────────────────────────────────────

_STATE_KEY_EVENT_BUS = "_event_bus"
_STATE_KEY_EVENT_BUS_V2 = "_event_bus_v2"
_STATE_KEY_SESSION_SERVICE = "_session_service"


# ── DB path ──────────────────────────────────────────────────────────


def _resolve_db_path() -> Path:
    """Return the SQLite DB path for the active workspace.

    Reads ``SR_WORKSPACE_PATH`` from env; falls back to ``~/.quantnodes``.
    """
    db_dir = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.home() / ".quantnodes")))
    return db_dir / "quantnodes_strategy_research_user.db"


def get_db_path(request: Request) -> Path:
    """FastAPI dependency: the active SQLite DB path.

    Resolution order:
    1. ``request.app.state.workspace_path`` (set in ``create_app``).
    2. ``SR_WORKSPACE_PATH`` environment variable.
    3. ``~/.quantnodes`` (default).

    Returns:
        Path to the SQLite database file.
    """
    workspace = getattr(request.app.state, "workspace_path", None)
    if workspace is not None:
        return Path(workspace) / "quantnodes_strategy_research_user.db"
    return _resolve_db_path()


# ── EventBus (legacy + V2) ──────────────────────────────────────────


def _ensure_event_bus_attached(event_bus: "EventBus") -> None:
    """One-time wire-up of SSE buffer to the legacy EventBus."""
    from .session.bridge import attach_eventbus_to_sse
    if not getattr(event_bus, "_sse_attached", False):
        attach_eventbus_to_sse(event_bus)
        event_bus._sse_attached = True  # type: ignore[attr-defined]


def get_event_bus(request: Request) -> "EventBus":
    """FastAPI dependency: shared legacy EventBus instance.

    Constructed once per process and cached on ``app.state``. The SSE
    bridge is attached lazily on first call.
    """
    bus = getattr(request.app.state, _STATE_KEY_EVENT_BUS, None)
    if bus is None:
        from .session.events import EventBus
        bus = EventBus()
        _ensure_event_bus_attached(bus)
        setattr(request.app.state, _STATE_KEY_EVENT_BUS, bus)
    return bus


def get_event_bus_v2(request: Request) -> "EventBusV2":
    """FastAPI dependency: shared EventBusV2 (triple-write)."""
    bus_v2 = getattr(request.app.state, _STATE_KEY_EVENT_BUS_V2, None)
    if bus_v2 is None:
        from .session.event_bus_v2 import EventBusV2
        legacy_bus = get_event_bus(request)
        db_path = get_db_path(request)
        bus_v2 = EventBusV2(legacy_bus, db_path, flush_to_messages=True)
        setattr(request.app.state, _STATE_KEY_EVENT_BUS_V2, bus_v2)
    return bus_v2


# ── SessionService ──────────────────────────────────────────────────


def get_session_store(request: Request) -> "SessionStore":
    """FastAPI dependency: shared SessionStore.

    Backed by SQLite at ``get_db_path(request)``.
    """
    from .session.store import SessionStore
    db_path = get_db_path(request)
    return SessionStore(db_path=db_path)


def get_session_service(request: Request) -> "SessionService":
    """FastAPI dependency: shared SessionService.

    Constructed once per process and cached on ``app.state``. Subsequent
    calls return the cached instance so all routers share state.
    """
    svc = getattr(request.app.state, _STATE_KEY_SESSION_SERVICE, None)
    if svc is None:
        from .session.service import SessionService
        store = get_session_store(request)
        bus_v2 = get_event_bus_v2(request)
        svc = SessionService(store=store, event_bus=bus_v2)
        setattr(request.app.state, _STATE_KEY_SESSION_SERVICE, svc)
    return svc


# ── Reset (testing utility) ─────────────────────────────────────────


def reset_app_state(app) -> None:
    """Clear all cached services from ``app.state``.

    Useful between test cases to ensure fresh state.
    """
    for key in (_STATE_KEY_EVENT_BUS, _STATE_KEY_EVENT_BUS_V2,
                _STATE_KEY_SESSION_SERVICE):
        if hasattr(app.state, key):
            delattr(app.state, key)


__all__ = [
    "get_db_path",
    "get_event_bus",
    "get_event_bus_v2",
    "get_session_service",
    "get_session_store",
    "reset_app_state",
]
