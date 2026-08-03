"""Phase 7 — EventStore: SQLite event_log 单一事实源 + cache + SSE push.

Replaces:
- ``api/session/events.py:EventBus`` (legacy in-memory buffer)
- ``api/session/event_bus_v2.py:EventBusV2`` (dual-write event_log + legacy)

Architecture:
- SQLite event_log is the single source of truth (matches memory_manager.py)
- In-memory cache for hot sessions (LRU, same SessionCache as memory)
- Optional SSE push callback (replaces legacy asyncio.Queue subscribers)

Auto-repair: same SQLite health_check + auto_repair as MemoryManager.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .cache import CacheConfig, SessionCache, SessionLockMap
from .memory_manager import InMemoryStore, SQLiteStore, resolve_db_path

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────


@dataclass
class EventV2:
    """Domain event stored in event_log table."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    aggregate_id: str = ""  # session_id
    seq: int = 0
    type: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    time_created: float = field(default_factory=time.time)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "aggregate_id": self.aggregate_id,
            "seq": self.seq,
            "type": self.type,
            "data_json": json.dumps(self.data, ensure_ascii=False),
            "time_created": self.time_created,
        }

    @classmethod
    def from_row(cls, row: tuple) -> "EventV2":
        data = {}
        try:
            data = json.loads(row[4])
        except Exception:
            pass
        return cls(
            id=row[0],
            aggregate_id=row[1],
            seq=row[2],
            type=row[3],
            data=data,
            time_created=row[5],
        )


# ── EventStore ──────────────────────────────────────────────────────


class EventStore:
    """SQLite event_log 单一事实源 + cache + SSE push.

    Drop-in replacement for ``EventBus`` + ``EventBusV2``. The same SQLite
    DB (``~/.quantnodes/sessions.db``) holds both ``messages`` and
    ``event_log`` tables — single physical file, two logical tables.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        cache_config: CacheConfig | None = None,
        sse_pusher: Callable[[str, EventV2], None] | None = None,
    ):
        self._db_path = resolve_db_path(db_path)
        cache_config = cache_config or CacheConfig.from_env()

        # 1. SQLite backend with auto-repair fallback
        sqlite_store = SQLiteStore(self._db_path)
        if not sqlite_store.health_check():
            logger.warning(
                "SQLite health check failed at %s; attempting auto-repair",
                self._db_path,
            )
            if not sqlite_store.auto_repair():
                logger.error(
                    "SQLite auto-repair failed; falling back to in-memory mode"
                )
                self._backend: SQLiteStore | InMemoryStore = InMemoryStore()
                self._degraded = True
            else:
                self._backend = sqlite_store
                self._degraded = False
        else:
            self._backend = sqlite_store
            self._degraded = False
        self._sqlite_store = sqlite_store if not self._degraded else None

        # 2. Cache for hot sessions
        self._cache_config = cache_config
        self._cache = SessionCache(cache_config)
        self._locks = SessionLockMap()

        # 3. SSE push callback (replaces asyncio.Queue subscribers)
        self._sse_pusher = sse_pusher

        # 4. Live subscribers (for subscribe() async iterator)
        self._live_queues: dict[str, list[asyncio.Queue]] = {}
        self._live_lock = threading.RLock()

        # 5. Initialize schema (idempotent)
        self._init_event_log_schema()

    def _init_event_log_schema(self) -> None:
        if isinstance(self._backend, InMemoryStore):
            # In-memory store doesn't persist; event_log lives only in cache
            return
        conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT,
                time_created REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate "
            "ON event_log(aggregate_id, seq)"
        )
        conn.commit()

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def backend(self):
        return self._backend

    # ── Public API ────────────────────────────────────────────────

    def emit(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> EventV2:
        """Emit event: SQLite → cache → SSE push + live subscribers.

        Same signature as legacy EventBus.emit(). Auto-assigns monotonic
        seq per session.
        """
        with self._backend._lock if hasattr(self._backend, "_lock") else _noop_cm():  # type: ignore[attr-defined]
            seq = self._next_seq(session_id)
            event = EventV2(
                aggregate_id=session_id,
                seq=seq,
                type=event_type,
                data=data or {},
            )
            try:
                self._persist(event)
            except Exception as exc:
                logger.exception("event_log persist failed: %s", exc)
                # Continue — at least push to live subscribers
                pass

        # Cache update
        self._cache.append(session_id, event)

        # SSE push callback
        if self._sse_pusher:
            try:
                self._sse_pusher(session_id, event)
            except Exception as exc:
                logger.warning("SSE push failed: %s", exc)

        # Live subscribers
        self._broadcast_live(session_id, event)

        return event

    def publish(self, event: EventV2) -> None:
        """Lower-level publish: takes a pre-built EventV2."""
        if not event.id:
            event.id = str(uuid.uuid4())
        if not event.time_created:
            event.time_created = time.time()
        with self._backend._lock if hasattr(self._backend, "_lock") else _noop_cm():  # type: ignore[attr-defined]
            try:
                self._persist(event)
            except Exception:
                logger.exception("event_log persist failed (publish)")
        self._cache.append(event.aggregate_id, event)
        if self._sse_pusher:
            try:
                self._sse_pusher(event.aggregate_id, event)
            except Exception:
                pass
        self._broadcast_live(event.aggregate_id, event)

    async def subscribe(self, session_id: str) -> AsyncIterator[EventV2]:
        """Async iterator: yield cache replay + live events as they arrive.

        Combines replay (catch up on missed events) + live (future events).
        Use ``break`` or ``CancelledError`` to stop.
        """
        # 1. Replay cache first
        cached = self._cache.get(session_id)
        if cached is not None:
            for ev in cached:
                yield ev

        # 2. Then replay any non-cached SQLite tail (cross-process writes)
        try:
            tail = self._replay(session_id)
            last_cache_seq = cached[-1].seq if cached else 0
            for ev in tail:
                if ev.seq > last_cache_seq:
                    yield ev
        except Exception:
            pass

        # 3. Subscribe to live events
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._live_lock:
            self._live_queues.setdefault(session_id, []).append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        except asyncio.CancelledError:
            pass
        finally:
            with self._live_lock:
                queues = self._live_queues.get(session_id, [])
                if queue in queues:
                    queues.remove(queue)

    def replay(self, session_id: str, from_seq: int = 0) -> list[EventV2]:
        """Return all events for session with seq > from_seq."""
        return self._replay(session_id, from_seq=from_seq)

    def last_seq(self, session_id: str) -> int:
        """Return the highest seq for session (or 0)."""
        return self._next_seq(session_id) - 1

    def count(self, session_id: str | None = None) -> int:
        """Total events (optionally filtered by session)."""
        try:
            if hasattr(self._backend, "_ensure_conn"):
                conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
                if session_id is None:
                    return conn.execute("SELECT count(*) FROM event_log").fetchone()[0]
                return conn.execute(
                    "SELECT count(*) FROM event_log WHERE aggregate_id = ?",
                    (session_id,),
                ).fetchone()[0]
            else:
                # InMemoryStore
                data = getattr(self._backend, "_data", {})
                if session_id is None:
                    return sum(len(v) for v in data.values())
                return len(data.get(session_id, []))
        except Exception:
            return 0

    # ── Private helpers ───────────────────────────────────────────

    def _persist(self, event: EventV2) -> None:
        if isinstance(self._backend, InMemoryStore):
            # In-memory store: skip persistence
            return
        conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
        row = event.to_row()
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
            "time_created) VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["aggregate_id"], row["seq"], row["type"],
             row["data_json"], row["time_created"]),
        )
        conn.commit()

    def _replay(self, session_id: str, from_seq: int = 0) -> list[EventV2]:
        try:
            if hasattr(self._backend, "_ensure_conn"):
                conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
                rows = conn.execute(
                    "SELECT id, aggregate_id, seq, type, data_json, time_created "
                    "FROM event_log WHERE aggregate_id = ? AND seq > ? "
                    "ORDER BY seq ASC",
                    (session_id, from_seq),
                ).fetchall()
                return [EventV2.from_row(r) for r in rows]
            else:
                # InMemoryStore
                data = getattr(self._backend, "_data", {})
                return list(data.get(session_id, []))
        except Exception:
            return []

    def _next_seq(self, session_id: str) -> int:
        try:
            if hasattr(self._backend, "_ensure_conn"):
                conn = self._backend._ensure_conn()  # type: ignore[attr-defined]
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM event_log "
                    "WHERE aggregate_id = ?",
                    (session_id,),
                ).fetchone()
                return (row[0] if row else 0) + 1
            else:
                # InMemoryStore
                data = getattr(self._backend, "_data", {})
                msgs = data.get(session_id, [])
                return max((m.seq for m in msgs), default=0) + 1
        except Exception:
            return 1

    def _broadcast_live(self, session_id: str, event: EventV2) -> None:
        with self._live_lock:
            queues = list(self._live_queues.get(session_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Live queue full for session %s", session_id)

    def health_report(self) -> dict[str, Any]:
        return {
            "event_store": {
                "degraded": self.is_degraded,
                "backend": type(self._backend).__name__,
                "live_subscribers": sum(
                    len(q) for q in self._live_queues.values()
                ),
                "cache_session_count": self._cache.session_count,
            },
        }


class _noop_cm:
    """No-op context manager for backends without _lock attr."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ── EventStoreFactory ──────────────────────────────────────────────


_default_instance: EventStore | None = None


class EventStoreFactory:
    """Process-singleton factory for EventStore."""

    @classmethod
    def create(
        cls,
        db_path: Path | None = None,
        cache_config: CacheConfig | None = None,
        sse_pusher: Callable[[str, EventV2], None] | None = None,
    ) -> EventStore:
        global _default_instance
        if _default_instance is None:
            _default_instance = EventStore(
                db_path=db_path,
                cache_config=cache_config,
                sse_pusher=sse_pusher,
            )
        return _default_instance

    @classmethod
    def reset(cls) -> None:
        global _default_instance
        _default_instance = None


def get_default_event_store() -> EventStore:
    """Module-level accessor."""
    return EventStoreFactory.create()


__all__ = [
    "EventStore",
    "EventStoreFactory",
    "EventV2",
    "get_default_event_store",
]
