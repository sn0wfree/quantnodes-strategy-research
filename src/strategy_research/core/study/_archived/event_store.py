"""EventStore — Event Sourcing for Study state.

Replaces state.json snapshot with an append-only event stream.
Each state change is recorded as an immutable event, enabling:
- Full history replay
- Audit trail
- Time-travel debugging
- Crash recovery via event replay

Design inspired by Temporal's Event History and EventStoreDB.

Authority: design §3.2/A4 (upgraded from snapshot to event sourcing).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class EventType(str, Enum):
    """All study lifecycle events."""
    # Lifecycle
    STUDY_CREATED = "study.created"
    STUDY_STARTED = "study.started"
    STUDY_PAUSED = "study.paused"
    STUDY_RESUMED = "study.resumed"
    STUDY_CANCELLED = "study.cancelled"
    STUDY_COMPLETED = "study.completed"
    STUDY_ERROR = "study.error"
    STUDY_EARLY_STOPPED = "study.early_stopped"

    # Round
    ROUND_STARTED = "round.started"
    ROUND_COMPLETED = "round.completed"
    ROUND_DISCARDED = "round.discarded"
    ROUND_KEPT = "round.kept"
    ROUND_ABORDED = "round.aborted"

    # Phase
    PHASE_STARTED = "phase.started"
    PHASE_COMPLETED = "phase.completed"
    PHASE_FAILED = "phase.failed"

    # Agent
    AGENT_SPAWNED = "agent.spawned"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    # Backtest
    BACKTEST_STARTED = "backtest.started"
    BACKTEST_COMPLETED = "backtest.completed"
    BACKTEST_FAILED = "backtest.failed"

    # Metrics
    METRICS_UPDATED = "metrics.updated"
    METRICS_TARGETS_MET = "metrics.targets_met"

    # Budget
    BUDGET_UPDATED = "budget.updated"
    BUDGET_EXCEEDED = "budget.exceeded"

    # Review
    REVIEW_STARTED = "review.started"
    REVIEW_COMPLETED = "review.completed"
    REVIEW_DEVIATION = "review.deviation"

    # Knowledge
    KNOWLEDGE_COLLECTED = "knowledge.collected"
    KNOWLEDGE_GAP_DETECTED = "knowledge.gap_detected"

    # State
    STATE_SNAPSHOT = "state.snapshot"  # periodic snapshot for fast recovery

    # Signal
    SIGNAL_RECEIVED = "signal.received"
    TIMER_FIRED = "timer.fired"

    # Custom
    CUSTOM = "custom"


@dataclass(frozen=True)
class Event:
    """Immutable event record."""
    event_id: str
    event_type: EventType
    study_id: str
    timestamp: float  # time.time()
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1  # schema version for forward compatibility

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "study_id": self.study_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "metadata": self.metadata,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        return cls(
            event_id=d["event_id"],
            event_type=EventType(d["event_type"]),
            study_id=d["study_id"],
            timestamp=d["timestamp"],
            data=d.get("data", {}),
            metadata=d.get("metadata", {}),
            version=d.get("version", 1),
        )


class EventFilter:
    """Filter for querying events."""

    def __init__(
        self,
        event_types: list[EventType] | None = None,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int | None = None,
    ):
        self.event_types = event_types
        self.after_seq = after_seq
        self.before_seq = before_seq
        self.limit = limit


class EventStore:
    """Append-only event store for study state.

    Events are stored in two places:
    1. In-memory list (fast reads, lost on crash)
    2. SQLite database (persistent, crash-safe)

    On startup, events are replayed from SQLite to rebuild in-memory state.
    """

    def __init__(self, db_path: Path | None = None):
        self._events: list[Event] = []
        self._sequences: dict[str, int] = {}  # study_id -> next seq
        self._listeners: list[Callable[[Event], None]] = []
        self._lock = threading.RLock()
        self._db_path = db_path
        self._snapshots: dict[str, dict[str, Any]] = {}  # study_id -> latest snapshot

        if db_path:
            self._init_db()
            self._load_events_from_db()

    def _init_db(self) -> None:
        """Initialize SQLite database for persistent event storage."""
        import sqlite3
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                study_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                data TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                version INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_study_id ON events(study_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                study_id TEXT PRIMARY KEY,
                snapshot_data TEXT NOT NULL,
                last_seq INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _load_events_from_db(self) -> None:
        """Load events from SQLite database on initialization."""
        if not self._db_path:
            return

        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            rows = conn.execute(
                """SELECT event_id, event_type, study_id, timestamp, data, metadata, version
                   FROM events ORDER BY seq ASC"""
            ).fetchall()

            for row in rows:
                event = Event(
                    event_id=row[0],
                    event_type=EventType(row[1]),
                    study_id=row[2],
                    timestamp=row[3],
                    data=json.loads(row[4]),
                    metadata=json.loads(row[5]),
                    version=row[6],
                )
                self._events.append(event)

                # Update sequence counter
                if event.study_id not in self._sequences:
                    self._sequences[event.study_id] = 0
                self._sequences[event.study_id] += 1

        finally:
            conn.close()

    def append(
        self,
        event_type: EventType,
        study_id: str,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Event:
        """Append a new event to the store."""
        with self._lock:
            seq = self._sequences.get(study_id, 0) + 1
            self._sequences[study_id] = seq

            event = Event(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                study_id=study_id,
                timestamp=time.time(),
                data=data or {},
                metadata=metadata or {},
            )

            self._events.append(event)

            # Persist to SQLite
            if self._db_path:
                self._persist_event(event, seq)

            # Notify listeners
            for listener in self._listeners:
                try:
                    listener(event)
                except Exception:
                    pass  # Don't let listener errors affect the store

            return event

    def _persist_event(self, event: Event, seq: int) -> None:
        """Persist event to SQLite."""
        import sqlite3
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """INSERT INTO events (seq, event_id, event_type, study_id, timestamp, data, metadata, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    seq,
                    event.event_id,
                    event.event_type.value,
                    event.study_id,
                    event.timestamp,
                    json.dumps(event.data, ensure_ascii=False, default=str),
                    json.dumps(event.metadata, ensure_ascii=False, default=str),
                    event.version,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def query(
        self,
        study_id: str,
        filter: EventFilter | None = None,
    ) -> list[Event]:
        """Query events for a study."""
        with self._lock:
            events = [e for e in self._events if e.study_id == study_id]

        if filter:
            if filter.event_types:
                events = [e for e in events if e.event_type in filter.event_types]
            if filter.after_seq is not None:
                # Reconstruct sequence numbers
                seq_map = {e.event_id: i for i, e in enumerate(events)}
                events = [e for e in events if seq_map.get(e.event_id, 0) > filter.after_seq]
            if filter.limit is not None:
                events = events[-filter.limit:]

        return events

    def get_latest_snapshot(self, study_id: str) -> dict[str, Any] | None:
        """Get the latest snapshot for a study (for fast recovery)."""
        if study_id in self._snapshots:
            return self._snapshots[study_id]

        if self._db_path:
            import sqlite3
            conn = sqlite3.connect(str(self._db_path))
            try:
                row = conn.execute(
                    "SELECT snapshot_data, last_seq FROM snapshots WHERE study_id = ?",
                    (study_id,),
                ).fetchone()
                if row:
                    snapshot = json.loads(row[0])
                    self._snapshots[study_id] = snapshot
                    return snapshot
            finally:
                conn.close()

        return None

    def save_snapshot(self, study_id: str, state: dict[str, Any]) -> None:
        """Save a snapshot for fast recovery."""
        with self._lock:
            self._snapshots[study_id] = state

            if self._db_path:
                import sqlite3
                conn = sqlite3.connect(str(self._db_path))
                try:
                    seq = self._sequences.get(study_id, 0)
                    conn.execute(
                        """INSERT OR REPLACE INTO snapshots (study_id, snapshot_data, last_seq, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            study_id,
                            json.dumps(state, ensure_ascii=False, default=str),
                            seq,
                            time.time(),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()

    def replay(
        self,
        study_id: str,
        from_seq: int = 0,
        event_types: list[EventType] | None = None,
    ) -> list[Event]:
        """Replay events from a specific sequence number."""
        events = self.query(study_id, EventFilter(event_types=event_types))
        if from_seq > 0:
            events = events[from_seq:]
        return events

    def rebuild_state(self, study_id: str) -> dict[str, Any]:
        """Rebuild current state by replaying all events."""
        # Try snapshot first (fast path)
        snapshot = self.get_latest_snapshot(study_id)
        if snapshot:
            # Replay events after snapshot
            snapshot_seq = snapshot.get("_seq", 0)
            events = self.replay(study_id, from_seq=snapshot_seq)
            state = dict(snapshot)
        else:
            events = self.replay(study_id)
            state = {}

        # Apply events to rebuild state
        for event in events:
            state = self._apply_event(state, event)

        return state

    def _apply_event(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        """Apply an event to state (pure function)."""
        state = dict(state)
        data = event.data

        if event.event_type == EventType.STUDY_CREATED:
            state["study_id"] = event.study_id
            state["status"] = "created"
            state["created_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_STARTED:
            state["status"] = "running"
            state["started_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_PAUSED:
            state["status"] = "paused"
            state["paused_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_RESUMED:
            state["status"] = "running"
            state["resumed_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_CANCELLED:
            state["status"] = "cancelled"
            state["cancelled_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_COMPLETED:
            state["status"] = "complete"
            state["completed_at"] = event.timestamp

        elif event.event_type == EventType.STUDY_ERROR:
            state["status"] = "error"
            state["error"] = data.get("error")
            state["error_at"] = event.timestamp

        elif event.event_type == EventType.ROUND_STARTED:
            state["current_round"] = data.get("round", 0)
            state["round_started_at"] = event.timestamp

        elif event.event_type == EventType.ROUND_COMPLETED:
            state["last_completed_round"] = data.get("round", 0)
            state["last_verdict"] = data.get("verdict")
            state["last_metrics"] = data.get("metrics", {})

        elif event.event_type == EventType.ROUND_KEPT:
            state["last_keep_run_dir"] = data.get("run_dir")
            state["best_metrics"] = data.get("metrics", state.get("best_metrics", {}))

        elif event.event_type == EventType.METRICS_UPDATED:
            state["last_metrics"] = data.get("metrics", {})
            # Update best if improved
            metrics = data.get("metrics", {})
            best = state.get("best_metrics", {})
            if metrics.get("calmar", 0) > best.get("calmar", 0):
                state["best_metrics"] = metrics

        elif event.event_type == EventType.BUDGET_UPDATED:
            state["budget_used_turns"] = data.get("turns", 0)
            state["budget_used_time_s"] = data.get("time_s", 0.0)

        elif event.event_type == EventType.REVIEW_COMPLETED:
            state["last_review"] = data
            state["review_fail_count"] = data.get("fail_count", 0)

        elif event.event_type == EventType.KNOWLEDGE_COLLECTED:
            state["last_collect_round"] = data.get("round", 0)

        return state

    def add_listener(self, listener: Callable[[Event], None]) -> None:
        """Add an event listener."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Event], None]) -> None:
        """Remove an event listener."""
        with self._lock:
            self._listeners.remove(listener)

    def get_event_count(self, study_id: str) -> int:
        """Get the number of events for a study."""
        return len([e for e in self._events if e.study_id == study_id])

    def get_all_study_ids(self) -> list[str]:
        """Get all study IDs that have events."""
        return list({e.study_id for e in self._events})


# Global event store instance (singleton)
_global_store: EventStore | None = None
_global_lock = threading.Lock()


def get_event_store(db_path: Path | None = None) -> EventStore:
    """Get or create the global event store."""
    global _global_store
    with _global_lock:
        if _global_store is None:
            _global_store = EventStore(db_path)
        return _global_store


def reset_event_store() -> None:
    """Reset the global event store (for testing)."""
    global _global_store
    with _global_lock:
        _global_store = None
