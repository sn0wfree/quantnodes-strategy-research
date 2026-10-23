"""Projector — derive message state from event_log (Level 3, B3 commit 1).

This module is the read-side complement to EventBusV2. It reads events
from event_log and projects them into the same shape as messages +
message_parts (the Level 2 PartTable). The projector is the
authoritative source of message state in the event-sourced architecture.

In B3, the projector becomes the primary read path: SessionStore reads
messages via projector instead of directly from the messages table.
The messages + message_parts tables become materialized views (cached
projection), and event_log is the source of truth."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ...core.events.event_v2 import EventType, EventV2

logger = logging.getLogger(__name__)

# change_type → UI/LLM-facing label for goal messages
# ── Projected state (from projection_models) ─────────────────────────────────
from .projection_models import (
    ProjectedMessage,
    ProjectedSession,
)

# ── Projector ──────────────────────────────────────────────────────


class Projector:
    """Read-side projector: events → ProjectedSession.

    The projector is a pure function (with respect to the DB):
    it reads event_log and produces an in-memory state. The B1
    test suite verifies that the projected state matches the
    live DB (messages + message_parts) for synthetic event streams.

    In B2, a write-side flush() method will atomically UPDATE
    messages + message_parts to match the projected state. For
    B1, the API is read-only.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        # Reuse a single connection across flush calls to avoid the
        # overhead of creating a new connection every time and to
        # prevent "database is locked" errors from concurrent writers.
        self._conn: sqlite3.Connection | None = None
        # Serialize concurrent flush calls so two threads never
        # interleave their BEGIN/COMMIT on the same connection.
        self._flush_lock = threading.Lock()
        # Handler dispatch table: event_type → handler function
        # Per-session in-memory projections, keyed by session_id. Only
        # used by project_incremental(); project() remains a pure
        # function. Entries are invalidated on session deletion.
        # See docs/projector-incremental.md.
        self._cache: Dict[str, ProjectedSession] = {}

    def _get_conn(self) -> sqlite3.Connection:
        """Return a reusable connection, creating one on first call.

        ``check_same_thread=False`` is safe here: the ``_flush_lock``
        ensures only one thread uses the connection at a time.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
            )
        return self._conn

    def close(self) -> None:
        """Close the cached connection (idempotent).

        Owners with a finite lifetime (test fixtures, server shutdown)
        must call this before deleting the db file — sqlite keeps the
        file locked until the connection is closed, which fails temp
        cleanup on Windows with WinError 32.
        """
        with self._flush_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── Snapshot support (P0-1 B2) ─────────────────────────────────

    _SNAPSHOT_SCHEMA_DDL = (
        "CREATE TABLE IF NOT EXISTS snapshots ("
        "session_id TEXT PRIMARY KEY, "
        "seq INTEGER NOT NULL, "
        "snapshot_json TEXT NOT NULL, "
        "created_at REAL NOT NULL)"
    )
    _SNAPSHOT_INDEX_DDL = (
        "CREATE INDEX IF NOT EXISTS idx_snapshots_session_seq "
        "ON snapshots(session_id, seq)"
    )

    def _ensure_snapshots_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(self._SNAPSHOT_SCHEMA_DDL)
        conn.execute(self._SNAPSHOT_INDEX_DDL)

    def _save_snapshot(
        self,
        conn: sqlite3.Connection,
        state: ProjectedSession,
    ) -> None:
        """Persist ``state`` to ``snapshots`` (UPSERT, single-row).

        Called from ``flush()`` inside the existing transaction so the
        snapshot is consistent with the messages + message_parts it
        accompanies.
        """
        self._ensure_snapshots_table(conn)
        conn.execute(
            "INSERT INTO snapshots (session_id, seq, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "seq = excluded.seq, snapshot_json = excluded.snapshot_json, "
            "created_at = excluded.created_at "
            "WHERE excluded.seq > snapshots.seq",
            (
                state.session_id,
                state.last_seq,
                json.dumps(state.to_dict(), ensure_ascii=False),
                sqlite3.time.time(),
            ),
        )

    def _load_snapshot(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        after_seq: int,
    ) -> Optional[ProjectedSession]:
        """Return the most recent snapshot whose seq ≥ ``after_seq``."""
        self._ensure_snapshots_table(conn)
        row = conn.execute(
            "SELECT snapshot_json, seq FROM snapshots "
            "WHERE session_id = ? AND seq >= ? "
            "ORDER BY seq DESC LIMIT 1",
            (session_id, after_seq),
        ).fetchone()
        if row is None:
            return None
        return ProjectedSession.from_dict(json.loads(row[0]))

    # ── Incremental projection (see docs/projector-incremental.md) ─

    def project_incremental(
        self,
        session_id: str,
        collect_touched: bool = False,
    ) -> Tuple[ProjectedSession, Optional[Set[str]]]:
        """Extend the cached projection with only the new events.

        On a cache hit, only events after the cached ``last_seq`` are
        replayed (O(delta) instead of O(N)). On a cache miss, falls
        back to a full ``project()``.

        Returns ``(state, touched)``:
        - ``state``: the (mutated) cached ProjectedSession.
        - ``touched``: set of message ids modified by the new events,
          or None when ``collect_touched`` is False. Contains the
          sentinel ``"*"`` when an event rewrites the whole session
          (compact.ended with a replacement message list) — callers
          must then fall back to a full flush.
        """
        touched: Optional[Set[str]] = set() if collect_touched else None
        cached = self._cache.get(session_id)
        if cached is not None:
            events = self.load_events(session_id, after_seq=cached.last_seq)
            if touched is not None:
                for event in events:
                    self._collect_touched(event, touched)
            for event in events:
                self._apply(event, cached)
            if events:
                cached.last_seq = max(e.seq for e in events)
            return cached, touched

        # Cache miss — full rebuild. touched=None so the caller falls
        # back to a full flush (all rows written, stale rows deleted).
        state = self.project(session_id)
        self._cache[session_id] = state
        return state, None

    def invalidate(self, session_id: str) -> None:
        """Drop the cached projection for a session (session deleted).

        The next project_incremental() for this session does a full
        rebuild — correct because event_log is the source of truth.
        """
        self._cache.pop(session_id, None)

    def _collect_touched(self, event: EventV2, touched: Set[str]) -> None:
        """Record which message ids an event modifies (for delta flush).

        Every handler resolves its target message via
        ``data["message_id"]`` (or ``user_message_id`` for
        message_received), so those keys enumerate the touched set.
        Compact events with a full replacement list rewrite every
        message — signalled with the ``"*"`` sentinel.
        """
        data = event.data
        for key in ("message_id", "user_message_id"):
            mid = data.get(key)
            if mid:
                touched.add(mid)
        if event.type in (EventType.COMPACT, EventType.COMPACT_ENDED):
            if isinstance(data.get("messages"), list):
                touched.add("*")
            else:
                # Simple marker mode (L4 auto-compaction without
                # replacement list): explicitly record the marker id
                # so the delta flush actually writes it to the
                # messages table.  Without this, the marker is
                # created in the in-memory state but never persisted
                # because ``touched`` stays empty.
                marker_id = f"compact-{event.id[:8]}"
                touched.add(marker_id)

    @staticmethod
    def _message_row(msg: ProjectedMessage) -> Dict[str, Any]:
        """Serialize a single message to a messages-table row.

        Includes ``metadata_json`` so delta flushes carry agent_id (and
        any other metadata) — matching the full-flush path
        (``to_message_rows``). Without this, the first delta INSERT
        writes NULL and the UPSERT's COALESCE keeps it NULL forever.
        """
        return {
            "id": msg.id,
            "session_id": msg.session_id,
            "role": msg.role,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at,
            "seq": msg.seq,
            "metadata_json": json.dumps(msg.metadata, ensure_ascii=False)
            if msg.metadata else None,
        }

    @staticmethod
    def _part_rows(msg: ProjectedMessage) -> List[Dict[str, Any]]:
        """Serialize a single message's parts to message_parts rows."""
        return [
            {
                "id": p.id,
                "message_id": msg.id,
                "session_id": msg.session_id,
                "type": p.type,
                "data_json": json.dumps(p.data, ensure_ascii=False),
                "seq": p.seq,
                "time_created": p.time_created,
            }
            for p in msg.parts_in_order()
        ]

    # ── Public API ──────────────────────────────────────────────

    def load_events(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> List[EventV2]:
        """Read events from event_log for a session."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, aggregate_id, seq, type, data_json, time_created "
                "FROM event_log WHERE aggregate_id = ? AND seq > ? "
                "ORDER BY seq ASC",
                (session_id, after_seq),
            ).fetchall()
        finally:
            conn.close()
        return [EventV2.from_row(r) for r in rows]

    def project(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> ProjectedSession:
        """Build a ProjectedSession from event_log.

        Returns a fresh ProjectedSession each call. Multiple calls
        with the same args produce identical state (pure function).

        P0-1 B2: when a snapshot with ``seq >= after_seq`` exists, the
        snapshot seeds the state and only events after the snapshot's
        seq are replayed. Falls back to a full replay when no snapshot
        is available — same behaviour as before B2.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            snap = self._load_snapshot(conn, session_id, after_seq)
        finally:
            conn.close()
        if snap is not None:
            state = snap
            after_seq = snap.last_seq
        else:
            state = ProjectedSession(session_id=session_id)
        events = self.load_events(session_id, after_seq=after_seq)
        for event in events:
            self._apply(event, state)
        state.last_seq = max(
            (e.seq for e in events),
            default=after_seq,
        )
        return state

    def _apply(self, event: EventV2, state: ProjectedSession) -> None:
        """Dispatch one event to its handler.

        Delegates to projection_handlers.apply_event. Kept under
        this name: project_incremental and the projector tests call
        ``_apply`` directly.
        """
        from .projection_handlers import apply_event

        apply_event(event, state)

    def apply(self, event: EventV2, state: ProjectedSession) -> None:
        """Apply a single event to an existing ProjectedSession.

        Used by:
        - project() above to build state from a full event stream
        - Tests to verify individual event handling
        - Future: live projector that maintains state in memory
        """
        self._apply(event, state)

    # ── Flush (B2) ──────────────────────────────────────────────

    def flush(
        self,
        state: ProjectedSession,
        touched: Optional[Set[str]] = None,
        *,
        snapshot_interval: int = 200,
    ) -> None:
        """Atomically UPSERT the projected state to messages + message_parts.

        Idempotent: calling flush() twice with the same state produces
        the same result (no duplicate rows). Uses INSERT OR REPLACE
        for both messages and message_parts.

        Safety:
        - Runs in a single transaction
        - Only touches rows belonging to state.session_id
        - Deletes message_parts rows that no longer exist in the
          projection (handles part deletion edge cases)

        Args:
            touched: Optional set of message ids to write (delta
                flush, see docs/projector-incremental.md). Only those
                messages are UPSERTed and their parts rewritten.
                ``None`` or a set containing the ``"*"`` sentinel
                (whole-session rewrite, e.g. compact.ended) performs a
                full flush.

        This is the B2 write path. In B2, service.py publishes events
        via EventBusV2 which writes to event_log, then calls flush()
        to materialize the state to messages + message_parts for the
        existing read path (SessionStore.get_messages).

        Eventually (B3+), the read path will use the projector
        directly and messages + message_parts can be removed.
        """
        full = touched is None or "*" in touched
        msg_rows = state.to_message_rows() if full else None
        part_rows = state.to_part_rows() if full else None

        with self._flush_lock:
            conn = self._get_conn()
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN")

                if full:
                    # ── Full flush: UPSERT everything ──────────────
                    msg_ids = {row["id"] for row in msg_rows}
                    for row in msg_rows:
                        self._upsert_message(conn, row)

                    if msg_ids:
                        placeholders = ",".join("?" * len(msg_ids))
                        conn.execute(
                            f"DELETE FROM messages "
                            f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                            (state.session_id, *msg_ids),
                        )
                    else:
                        existing_count = conn.execute(
                            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                            (state.session_id,),
                        ).fetchone()[0]
                        if existing_count > 0:
                            logger.warning(
                                "flush: projection empty but DB has %d messages "
                                "for session %s — skipping DELETE "
                                "(possible event_log gap)",
                                existing_count, state.session_id,
                            )
                        else:
                            conn.execute(
                                "DELETE FROM messages WHERE session_id = ?",
                                (state.session_id,),
                            )

                    # UPSERT message_parts
                    for row in part_rows:
                        self._insert_part(conn, row)

                    # Delete parts that no longer exist (session scope)
                    part_ids = {r["id"] for r in part_rows}
                    if part_ids:
                        placeholders = ",".join("?" * len(part_ids))
                        conn.execute(
                            f"DELETE FROM message_parts "
                            f"WHERE session_id = ? AND id NOT IN ({placeholders})",
                            (state.session_id, *part_ids),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM message_parts WHERE session_id = ?",
                            (state.session_id,),
                        )
                else:
                    # ── Delta flush: only touched messages ──────────
                    for mid in sorted(touched):
                        msg = state.messages.get(mid)
                        if msg is None:
                            continue
                        self._upsert_message(conn, self._message_row(msg))
                        conn.execute(
                            "DELETE FROM message_parts WHERE message_id = ?",
                            (mid,),
                        )
                        for row in self._part_rows(msg):
                            self._insert_part(conn, row)

                # P0-1 B2: snapshot persistence
                if (
                    snapshot_interval > 0
                    and state.last_seq > 0
                    and state.last_seq % snapshot_interval == 0
                ):
                    self._save_snapshot(conn, state)

                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    @staticmethod
    def _upsert_message(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """UPSERT a single messages-table row (preserves metadata_json)."""
        metadata_json = row.get("metadata_json")
        conn.execute(
            "INSERT INTO messages "
            "(id, session_id, role, content, created_at, "
            "message_type, seq, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "session_id=excluded.session_id, "
            "role=excluded.role, "
            "content=excluded.content, "
            "created_at=excluded.created_at, "
            "message_type=excluded.message_type, "
            "seq=excluded.seq, "
            "metadata_json=COALESCE(excluded.metadata_json, "
            "                      messages.metadata_json)",
            (
                row["id"],
                row["session_id"],
                row["role"],
                row["content"],
                row["created_at"],
                row["message_type"],
                row["seq"],
                metadata_json,
            ),
        )

    @staticmethod
    def _insert_part(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
        """INSERT OR REPLACE a single message_parts row."""
        conn.execute(
            "INSERT OR REPLACE INTO message_parts "
            "(id, message_id, session_id, type, data_json, "
            "seq, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["message_id"],
                row["session_id"],
                row["type"],
                row["data_json"],
                row["seq"],
                row["time_created"],
            ),
        )

    def project_and_flush(self, session_id: str) -> ProjectedSession:
        """Convenience: project + flush in one call."""
        state = self.project(session_id)
        self.flush(state)
        return state

    def project_to_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[Any]:
        """Project event_log → list[Message] (B3 primary read path).

        Drop-in replacement for SessionStore.get_messages(). Returns
        the same Message objects with parts in metadata["_parts"].

        Args:
            session_id: Session to project.
            limit: Max messages to return (first N by seq, matching
                SessionStore.get_messages behavior).

        Returns:
            List of Message in chronological order (by seq).
        """
        state = self.project(session_id)
        messages = state.to_messages()
        if limit and len(messages) > limit:
            # First N by seq, matching SessionStore.get_messages() /
            # the DB read path (ORDER BY seq ASC LIMIT N).
            messages = messages[:limit]
        return messages

    # ── Event handlers ──────────────────────────────────────────

