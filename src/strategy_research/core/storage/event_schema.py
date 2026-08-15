"""Canonical ``event_log`` schema (P0-1 A1).

Single source of truth for the ``event_log`` DDL, previously duplicated in
three places with a creeping divergence:

- ``api/routers/web_session.py`` — canonical: FK, ``UNIQUE (aggregate_id, seq)``,
  ``data_json NOT NULL``, both indexes.
- ``core/agent/event_store.py`` — simplified: missing FK / UNIQUE / NOT NULL /
  the ``(type, time_created)`` index.
- ``api/session/backfill_event_log.py`` — middle: UNIQUE but no FK / second index.

Every writer / creator of ``event_log`` routes through
``ensure_event_log_schema()`` so the historical "whoever creates first wins"
schema divergence (``sessions`` / ``messages`` / ``event_log``) can never recur.

P0-1 additions (Phase A3): ``parent_event_id`` (trace tree) and ``branch_id``
(fork support). Existing DBs are backfilled via ``ALTER TABLE`` inside
``ensure_event_log_schema`` (idempotent); fresh DBs get the columns in DDL.
The ``UNIQUE (aggregate_id, branch_id, seq)`` rebuild for pre-existing tables
is handled by the versioned migration in Phase A4.
"""

from __future__ import annotations

import sqlite3

__all__ = ["EVENT_LOG_DDL", "ensure_event_log_schema"]

# The canonical event_log DDL. FK to sessions(id) is declared here for both
# creation orders; enforcement is per-connection (EventStore's SQLiteStore
# intentionally leaves foreign_keys OFF — see memory_manager._ensure_conn —
# so it can write events for sessions without a sessions row yet, while the
# web_session connection owns FK enforcement).
EVENT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS event_log (
    id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    time_created REAL NOT NULL,
    parent_event_id TEXT,
    branch_id TEXT NOT NULL DEFAULT 'main',
    FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (aggregate_id, seq)
)
"""

EVENT_LOG_INDEXES = (
    (
        "idx_event_log_aggregate_seq",
        "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_seq "
        "ON event_log(aggregate_id, seq)",
    ),
    (
        "idx_event_log_type_time",
        "CREATE INDEX IF NOT EXISTS idx_event_log_type_time "
        "ON event_log(type, time_created)",
    ),
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _add_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    ddl: str,
) -> None:
    """Idempotently add a column to an existing table."""
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_event_log_schema(conn: sqlite3.Connection) -> None:
    """Create / reconcile the ``event_log`` table and its indexes.

    Idempotent. Safe on fresh DBs (full canonical DDL), pre-existing DBs
    (backfills ``parent_event_id`` / ``branch_id`` via ALTER TABLE), and
    degraded in-memory backends (no-op if the connection has no table).

    Does NOT set ``PRAGMA foreign_keys`` — that is the caller's concern
    (web_session enables it; EventStore intentionally does not).
    """
    conn.execute(EVENT_LOG_DDL)

    # Backfill P0-1 columns for pre-existing event_log tables.
    _add_column(conn, "event_log", "parent_event_id", "parent_event_id TEXT")
    _add_column(
        conn,
        "event_log",
        "branch_id",
        "branch_id TEXT NOT NULL DEFAULT 'main'",
    )

    for _name, ddl in EVENT_LOG_INDEXES:
        conn.execute(ddl)
    conn.commit()
