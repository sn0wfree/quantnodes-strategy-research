"""EventBusV2 — dual-write event publisher (Level 3, B1 commit 3).

This module sits between the producers (AgentLoop, service.py) and
two sinks:
  1. event_log table (persistence — the source of truth for replay)
  2. Legacy EventBus (live SSE delivery to connected clients)

Both sinks receive the same event. The legacy EventBus is unchanged;
EventBusV2 is purely additive. This means:
- Phase 3 B1: service.py can opt in to use EventBusV2 instead of
  event_bus.emit. Old behavior preserved; events additionally land in
  event_log.
- Phase 3 B2: SSE handler can be enhanced to replay from event_log
  for late-rejoining clients (using the last_event_id).
- Phase 3 B3: legacy EventBus can be removed once the projector is
  the sole source of SSE state.

Why dual-write:
- Replay-after-restart: if the process crashes mid-iteration, the
  event_log preserves all events; subscribers that reconnect can
  request replay via last_event_id.
- Decoupling: the projector (B1.4) can subscribe to event_log
  without holding a reference to the EventBus. The projector just
  reads the log; it doesn't care HOW events got there.
- Revert: at any point during B2/B3, EventBusV2 can be swapped
  back for direct event_bus.emit. Just two import lines.

This module does NOT yet wire into service.py — that's a separate
commit. The test suite verifies the dual-write behavior in isolation.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from .event_v2 import EventV2, is_known_event_type
from .events import EventBus, SSEEvent

logger = logging.getLogger(__name__)


class EventBusV2:
    """Dual-write event publisher (event_log + EventBus).

    Attributes:
        event_bus: Legacy EventBus for SSE delivery. Unchanged.
        db_path: Path to SQLite DB (for event_log).
    """

    def __init__(
        self,
        event_bus: EventBus,
        db_path: Path,
    ) -> None:
        self.event_bus = event_bus
        self.db_path = Path(db_path)
        self._lock = threading.Lock()

    def publish(self, event: EventV2) -> None:
        """Publish to event_log AND forward to legacy EventBus.

        Args:
            event: EventV2 to publish.
        """
        # Validate (forward-compat: log + continue on unknown types)
        if not is_known_event_type(event.type):
            logger.debug(
                "EventBusV2: unknown event type %r (will persist but projector may skip)",
                event.type,
            )

        # Sink 1: persist to event_log (the source of truth)
        self._persist(event)

        # Sink 2: forward to legacy EventBus (live SSE)
        self._forward(event)

    def publish_batch(self, events: List[EventV2]) -> None:
        """Publish multiple events as a single transaction.

        Useful for replay (loading events from event_log and re-emitting
        them) or for bulk operations like L4 compaction finalization.
        """
        if not events:
            return
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                for event in events:
                    row = event.to_row()
                    conn.execute(
                        "INSERT INTO event_log (id, aggregate_id, seq, "
                        "type, data_json, time_created) VALUES "
                        "(?, ?, ?, ?, ?, ?)",
                        (row["id"], row["aggregate_id"], row["seq"],
                         row["type"], row["data_json"], row["time_created"]),
                    )
                conn.commit()
            finally:
                conn.close()

        # Forward all events to SSE
        for event in events:
            self._forward(event)

    def _persist(self, event: EventV2) -> None:
        """INSERT event into event_log table.

        Best-effort: if the DB write fails, log and continue. The SSE
        forward still happens (so live clients see the event) but
        replay will be missing this event. For production, we'd want
        a retry queue; for B1 we keep it simple.
        """
        try:
            row = event.to_row()
            with self._lock:
                conn = sqlite3.connect(str(self.db_path))
                try:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute(
                        "INSERT INTO event_log (id, aggregate_id, seq, "
                        "type, data_json, time_created) VALUES "
                        "(?, ?, ?, ?, ?, ?)",
                        (row["id"], row["aggregate_id"], row["seq"],
                         row["type"], row["data_json"], row["time_created"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except sqlite3.IntegrityError as exc:
            # Likely a UNIQUE (aggregate_id, seq) violation — same seq reused
            logger.error(
                "EventBusV2: seq collision for aggregate=%s seq=%s: %s",
                event.aggregate_id, event.seq, exc,
            )
        except sqlite3.Error as exc:
            logger.error(
                "EventBusV2: persist failed for event %s: %s",
                event.id, exc,
            )

    def _forward(self, event: EventV2) -> None:
        """Forward to legacy EventBus as SSEEvent.

        The SSE event id matches the event_log event id so that
        last_event_id recovery works seamlessly across both sinks.
        """
        sse_event = SSEEvent(
            event_id=event.id,
            event_type=event.type,
            data=event.data,
            session_id=event.aggregate_id,
            timestamp=event.time_created,
        )
        self.event_bus.publish(sse_event)

    # ── Replay support ──────────────────────────────────────────────
    #
    # Used by:
    # 1. SSE handler (Phase 3 B2) to recover missed events after
    #    client reconnect
    # 2. Projector (Phase 3 B1.4) to rebuild message state from
    #    event_log
    # 3. Tests to verify round-trip persistence

    def replay(
        self,
        session_id: str,
        after_seq: int = 0,
        limit: Optional[int] = None,
    ) -> List[EventV2]:
        """Read events from event_log in seq order.

        Args:
            session_id: Aggregate (session) ID to replay.
            after_seq: Return only events with seq > after_seq (for
                resume-after-disconnect). 0 means "all events".
            limit: Optional cap on number of events returned.

        Returns:
            List of EventV2 ordered by seq ASC.
        """
        sql = (
            "SELECT id, aggregate_id, seq, type, data_json, time_created "
            "FROM event_log WHERE aggregate_id = ? AND seq > ? "
            "ORDER BY seq ASC"
        )
        params: tuple = (session_id, after_seq)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, after_seq, limit)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [EventV2.from_row(r) for r in rows]

    def last_seq(self, session_id: str) -> int:
        """Return the highest seq stored for this session, or 0."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT MAX(seq) AS max_seq FROM event_log "
                "WHERE aggregate_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def count(self, session_id: Optional[str] = None) -> int:
        """Return event_log row count. If session_id given, only that session."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            if session_id is None:
                row = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
                    (session_id,),
                ).fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else 0


__all__ = ["EventBusV2"]
