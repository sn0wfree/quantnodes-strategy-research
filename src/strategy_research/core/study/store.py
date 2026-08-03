"""SQLite-backed store for study execution records.

Shares the goal ledger database (``goals.db`` by default) and adds a
``studies`` table tracking the autoresearch execution state for each
study. The ledger rows (``goals`` / ``goal_criteria`` / ``goal_evidence``)
remain owned by ``GoalStore``; this module only touches ``studies``.

See ``docs/study-longhorizon-plan.md`` for the design.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from .models import (
    ACTIVE_EXECUTION_STATUSES,
    MetricTarget,
    StudyDirective,
    StudyRecord,
    StudyStatus,
)

logger = logging.getLogger(__name__)

# Reuse the goal ledger DB by default so studies live next to the goals
# they are bound to; override via the same env var.
_DEFAULT_DB_PATH = Path.home() / ".quantnodes-research" / "goals.db"
_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, default: object) -> object:
    if not value:
        return default
    return json.loads(value)


def _default_db_path() -> Path:
    """Return the configured study/goal database path.

    Resolution order mirrors ``GoalStore``:
        1. ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` environment variable
        2. ``~/.quantnodes-research/goals.db``
    """

    raw_path = os.environ.get(_DB_PATH_ENV, "").strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return _DEFAULT_DB_PATH


F = TypeVar("F", bound=Callable)


def _synchronized(method: F) -> F:
    """Serialize access to the shared SQLite connection."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-typed-def]
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class StudyStore:
    """SQLite-backed store for study execution records.

    Like ``GoalStore``, owns one connection for its lifetime; per-request
    callers should use the context manager (``with StudyStore() as store:``).
    """

    _SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.RLock()
        self._init_db()

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""

        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    def __enter__(self) -> "StudyStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── schema ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create the ``studies`` + ``study_directives`` tables + indexes if absent."""

        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS studies (
                    study_id                TEXT PRIMARY KEY,
                    session_id              TEXT NOT NULL,
                    goal_id                 TEXT,
                    objective               TEXT NOT NULL,
                    executor_type           TEXT NOT NULL DEFAULT 'autoresearch',
                    workspace_path          TEXT NOT NULL,
                    strategy_name           TEXT NOT NULL,
                    metric_targets          TEXT,
                    budget_token            INTEGER,
                    budget_turn             INTEGER,
                    budget_time_seconds     INTEGER,
                    cooldown_base           REAL NOT NULL DEFAULT 30.0,
                    cooldown_jitter         REAL NOT NULL DEFAULT 10.0,
                    min_cooldown            REAL NOT NULL DEFAULT 1.0,
                    max_rounds              INTEGER,
                    lazy_detection_interval INTEGER NOT NULL DEFAULT 10,
                    keep_recent             INTEGER NOT NULL DEFAULT 10,
                    behavior                TEXT,
                    execution_status        TEXT NOT NULL DEFAULT 'queued',
                    current_round           INTEGER NOT NULL DEFAULT 0,
                    last_metrics            TEXT,
                    last_verdict            TEXT,
                    last_error              TEXT,
                    heartbeat               TEXT,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL,
                    completed_at            TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_studies_session "
                "ON studies(session_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_studies_status "
                "ON studies(execution_status)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS study_directives (
                    directive_id TEXT PRIMARY KEY,
                    study_id     TEXT NOT NULL,
                    content      TEXT NOT NULL,
                    issued_by    TEXT,
                    created_at   TEXT NOT NULL,
                    consumed_at  TEXT,
                    FOREIGN KEY (study_id) REFERENCES studies(study_id)
                        ON DELETE CASCADE
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_directives_study "
                "ON study_directives(study_id, consumed_at)"
            )

    # ── writes ───────────────────────────────────────────────────────

    @_synchronized
    def create_study(
        self,
        *,
        session_id: str,
        goal_id: str | None,
        objective: str,
        workspace_path: str,
        strategy_name: str,
        executor_type: str = "autoresearch",
        metric_targets: list[dict] | None = None,
        budget_token: int | None = None,
        budget_turn: int | None = None,
        budget_time_seconds: int | None = None,
        cooldown_base: float = 30.0,
        cooldown_jitter: float = 10.0,
        min_cooldown: float = 1.0,
        max_rounds: int | None = None,
        lazy_detection_interval: int = 10,
        keep_recent: int = 10,
        behavior: str | None = None,
    ) -> StudyRecord:
        """Insert a new study row (status=queued).

        Caller is responsible for creating the goal ledger row (via
        ``GoalStore.replace_goal``) and passing the returned
        ``goal_id`` here so the study links to it.
        """

        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if not objective.strip():
            raise ValueError("objective must not be empty")
        if not workspace_path.strip():
            raise ValueError("workspace_path must not be empty")
        if not strategy_name.strip():
            raise ValueError("strategy_name must not be empty")
        if executor_type not in ("autoresearch", "workflow"):
            raise ValueError(
                f"Unknown executor_type: {executor_type!r} "
                "(expected 'autoresearch' or 'workflow')"
            )
        for name, value in (
            ("cooldown_base", cooldown_base),
            ("cooldown_jitter", cooldown_jitter),
            ("min_cooldown", min_cooldown),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        for name, value in (
            ("budget_token", budget_token),
            ("budget_turn", budget_turn),
            ("budget_time_seconds", budget_time_seconds),
            ("max_rounds", max_rounds),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")

        study_id = _id("study")
        now = _now_iso()
        targets_json = _json_dumps(metric_targets or [])

        with self._write_transaction():
            self._conn.execute(
                """
                INSERT INTO studies (
                    study_id, session_id, goal_id, objective,
                    executor_type, workspace_path, strategy_name,
                    metric_targets,
                    budget_token, budget_turn, budget_time_seconds,
                    cooldown_base, cooldown_jitter, min_cooldown,
                    max_rounds, lazy_detection_interval, keep_recent,
                    behavior,
                    execution_status, current_round,
                    heartbeat, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    study_id,
                    session_id,
                    goal_id,
                    objective,
                    executor_type,
                    workspace_path,
                    strategy_name,
                    targets_json,
                    budget_token,
                    budget_turn,
                    budget_time_seconds,
                    cooldown_base,
                    cooldown_jitter,
                    min_cooldown,
                    max_rounds,
                    lazy_detection_interval,
                    keep_recent,
                    behavior,
                    now,
                    now,
                    now,
                ),
            )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        logger.info("study created: %s (session=%s goal=%s)", study_id, session_id, goal_id)
        return self._study_from_row(row)

    @_synchronized
    def update_execution_status(
        self,
        study_id: str,
        status: StudyStatus,
        *,
        last_error: str | None = None,
        last_metrics: dict | None = None,
        last_verdict: str | None = None,
    ) -> StudyRecord | None:
        """Transition a study's execution status.

        Sets ``completed_at`` for terminal statuses; returns the updated
        record (or ``None`` when the study no longer exists).
        """

        now = _now_iso()
        terminals = {
            StudyStatus.COMPLETE,
            StudyStatus.CANCELLED,
            StudyStatus.ERROR,
            StudyStatus.BUDGET_LIMITED,
        }
        completed_at = now if status in terminals else None

        sets = [
            "execution_status = ?",
            "updated_at = ?",
            "heartbeat = ?",
        ]
        params: list[Any] = [status.value, now, now]
        if last_error is not None:
            sets.append("last_error = ?")
            params.append(last_error)
        if last_metrics is not None:
            sets.append("last_metrics = ?")
            params.append(_json_dumps(last_metrics))
        if last_verdict is not None:
            sets.append("last_verdict = ?")
            params.append(last_verdict)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        params.append(study_id)

        with self._write_transaction():
            self._conn.execute(
                f"UPDATE studies SET {', '.join(sets)} WHERE study_id = ?",
                params,
            )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @_synchronized
    def update_round_heartbeat(self, study_id: str, current_round: int) -> None:
        """Bump the round counter + heartbeat timestamp (best-effort)."""

        now = _now_iso()
        with self._write_transaction():
            self._conn.execute(
                "UPDATE studies SET current_round = ?, heartbeat = ?, updated_at = ? "
                "WHERE study_id = ?",
                (current_round, now, now, study_id),
            )

    @_synchronized
    def update_last_metrics(
        self, study_id: str, metrics: dict, verdict: str
    ) -> None:
        """Record the most recent round's metrics + keep/discard verdict."""

        now = _now_iso()
        with self._write_transaction():
            self._conn.execute(
                "UPDATE studies SET last_metrics = ?, last_verdict = ?, "
                "heartbeat = ?, updated_at = ? WHERE study_id = ?",
                (_json_dumps(metrics), verdict, now, now, study_id),
            )

    # ── reads ────────────────────────────────────────────────────────

    @_synchronized
    def get_study(self, study_id: str) -> StudyRecord | None:
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @_synchronized
    def get_active_study(self, session_id: str) -> StudyRecord | None:
        """Return the session's currently-running/queued/paused study, if any."""

        placeholders = ",".join("?" for _ in ACTIVE_EXECUTION_STATUSES)
        statuses = [s.value for s in ACTIVE_EXECUTION_STATUSES]
        row = self._conn.execute(
            f"SELECT * FROM studies WHERE session_id = ? "
            f"AND execution_status IN ({placeholders}) "
            f"ORDER BY created_at ASC LIMIT 1",
            (session_id, *statuses),
        ).fetchone()
        return self._study_from_row(row) if row else None

    @_synchronized
    def list_studies(
        self,
        session_id: str | None = None,
        status: StudyStatus | None = None,
        limit: int = 100,
    ) -> list[StudyRecord]:
        """List studies, optionally filtered; newest first."""

        query = "SELECT * FROM studies WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if status:
            query += " AND execution_status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._study_from_row(row) for row in rows]

    @_synchronized
    def list_active_studies(self) -> list[StudyRecord]:
        """Return all studies in a non-terminal execution status.

        Used by the scheduler at startup to recover interrupted runs.
        """

        placeholders = ",".join("?" for _ in ACTIVE_EXECUTION_STATUSES)
        statuses = [s.value for s in ACTIVE_EXECUTION_STATUSES]
        rows = self._conn.execute(
            f"SELECT * FROM studies WHERE execution_status IN ({placeholders}) "
            f"ORDER BY created_at ASC",
            statuses,
        ).fetchall()
        return [self._study_from_row(row) for row in rows]

    @_synchronized
    def delete_session_studies(self, session_id: str) -> int:
        """Delete all study rows for a session. Returns the count removed."""

        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        with self._write_transaction():
            row = self._conn.execute(
                "SELECT COUNT(*) FROM studies WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            count = int(row[0]) if row else 0
            # Directives are cascaded via FK ON DELETE CASCADE.
            self._conn.execute(
                "DELETE FROM studies WHERE session_id = ?", (session_id,)
            )
        return count

    # ── directives (Phase 2: mid-execution interaction) ─────────────

    @_synchronized
    def add_directive(
        self,
        study_id: str,
        content: str,
        *,
        issued_by: str | None = None,
    ) -> StudyDirective:
        """Append a research directive to a study's pending queue.

        The next round's researcher agent sees the directive in its prompt
        context. ``issued_by`` is opaque (session user id, "api", etc.) for
        audit purposes.
        """
        if not content.strip():
            raise ValueError("directive content must not be empty")
        # Validate the study exists; FK would catch it but a clean error is
        # friendlier for the API/command surface.
        study = self.get_study(study_id)
        if study is None:
            raise ValueError(f"study not found: {study_id}")

        directive_id = _id("dir")
        now = _now_iso()
        with self._write_transaction():
            self._conn.execute(
                """
                INSERT INTO study_directives (
                    directive_id, study_id, content, issued_by,
                    created_at, consumed_at
                )
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (directive_id, study_id, content.strip(), issued_by, now),
            )
        return StudyDirective(
            directive_id=directive_id,
            study_id=study_id,
            content=content.strip(),
            issued_by=issued_by,
            created_at=now,
            consumed_at=None,
        )

    @_synchronized
    def list_pending_directives(
        self, study_id: str
    ) -> list[StudyDirective]:
        """Return all directives for the study not yet consumed."""
        rows = self._conn.execute(
            """
            SELECT directive_id, study_id, content, issued_by, created_at,
                   consumed_at
            FROM study_directives
            WHERE study_id = ? AND consumed_at IS NULL
            ORDER BY created_at ASC
            """,
            (study_id,),
        ).fetchall()
        return [
            StudyDirective(
                directive_id=row["directive_id"],
                study_id=row["study_id"],
                content=row["content"],
                issued_by=row["issued_by"],
                created_at=row["created_at"],
                consumed_at=row["consumed_at"],
            )
            for row in rows
        ]

    @_synchronized
    def mark_directives_consumed(
        self, study_id: str, directive_ids: list[str]
    ) -> int:
        """Mark ``directive_ids`` consumed. Returns count updated.

        ``directive_ids`` must all belong to the same study. The executor
        should pass only the ids it actually injected so any race-written
        directives stay pending for the next round.
        """
        if not directive_ids:
            return 0
        now = _now_iso()
        placeholders = ",".join("?" for _ in directive_ids)
        with self._write_transaction():
            cur = self._conn.execute(
                f"UPDATE study_directives "
                f"SET consumed_at = ? "
                f"WHERE study_id = ? AND directive_id IN ({placeholders}) "
                f"AND consumed_at IS NULL",
                [now, study_id, *directive_ids],
            )
        return cur.rowcount

    # ── internals ────────────────────────────────────────────────────

    @contextmanager
    def _write_transaction(self):
        """Open an immediate write transaction for cross-connection safety."""

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    @staticmethod
    def _study_from_row(row: sqlite3.Row) -> StudyRecord:
        """Materialize a ``StudyRecord`` from a SQLite row."""

        targets_raw = _json_loads(row["metric_targets"], [])
        # Normalize: tolerate either list[dict] or already-typed shapes.
        if isinstance(targets_raw, list):
            metric_targets = [
                t if isinstance(t, dict) else MetricTarget(**t).as_dict()
                for t in targets_raw
            ]
        else:
            metric_targets = []

        metrics_raw = _json_loads(row["last_metrics"], None) if row["last_metrics"] else None

        return StudyRecord(
            study_id=row["study_id"],
            session_id=row["session_id"],
            goal_id=row["goal_id"],
            objective=row["objective"],
            executor_type=row["executor_type"],
            workspace_path=row["workspace_path"],
            strategy_name=row["strategy_name"],
            metric_targets=metric_targets,
            budget_token=row["budget_token"],
            budget_turn=row["budget_turn"],
            budget_time_seconds=row["budget_time_seconds"],
            cooldown_base=row["cooldown_base"],
            cooldown_jitter=row["cooldown_jitter"],
            min_cooldown=row["min_cooldown"],
            max_rounds=row["max_rounds"],
            lazy_detection_interval=row["lazy_detection_interval"],
            keep_recent=row["keep_recent"],
            behavior=row["behavior"],
            execution_status=StudyStatus(row["execution_status"]),
            current_round=row["current_round"],
            last_metrics=metrics_raw if isinstance(metrics_raw, dict) else None,
            last_verdict=row["last_verdict"],
            last_error=row["last_error"],
            heartbeat=row["heartbeat"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )