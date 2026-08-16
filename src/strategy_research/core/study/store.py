"""SQLite-backed store for study execution records.

Shares the goal ledger database (``goals.db`` by default) and adds a
``studies`` table tracking the autoresearch execution state for each
study. The ledger rows (``goals`` / ``goal_criteria`` / ``goal_evidence``)
remain owned by ``GoalStore``; this module only touches ``studies``.

See ``docs/study-longhorizon-plan.md`` for the design.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..storage.sqlite import (
    connect,
    json_dumps,
    json_loads,
    new_id,
    now_iso,
    resolve_db_path,
    synchronized,
    write_transaction,
)
from .models import (
    ACTIVE_EXECUTION_STATUSES,
    MetricTarget,
    StudyDirective,
    StudyRecord,
    StudyRoundRecord,
    StudyStatus,
)

logger = logging.getLogger(__name__)


def _dlog(module: str, msg: str, *args) -> None:
    """Dual-output log: logger + stderr so both file and terminal see it."""
    msg_fmt = msg % args if args else msg
    logger.info("[STUDY:%s] %s", module, msg_fmt)
    print(f"[STUDY:{module}] {msg_fmt}", flush=True)  # noqa: T201

# Reuse the goal ledger DB by default so studies live next to the goals
# they are bound to; override via the same env var.
_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"


def _default_db_path() -> Path:
    """Return the configured study/goal database path.

    Resolution order mirrors ``GoalStore``:
        1. ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` environment variable
        2. ``~/.quantnodes-research/goals.db``
    """
    return resolve_db_path("goals.db", _DB_PATH_ENV)


class StudyStore:
    """SQLite-backed store for study execution records.

    Like ``GoalStore``, owns one connection for its lifetime; per-request
    callers should use the context manager (``with StudyStore() as store:``).
    """

    _SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self.db_path)
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
                    last_traceback          TEXT,
                    heartbeat               TEXT,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL,
                    completed_at            TEXT,
                    monitor_interval_seconds INTEGER,
                    last_monitor_check_at   TEXT,
                    monitor_drift_count     INTEGER NOT NULL DEFAULT 0
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
            # v2 migration: owner_session_id — creator chat session kept for
            # goal-ledger writes while session_id may become "study:{id}".
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(studies)")]
            if "owner_session_id" not in cols:
                self._conn.execute(
                    "ALTER TABLE studies ADD COLUMN owner_session_id TEXT"
                )
                self._conn.execute(
                    "UPDATE studies SET owner_session_id = session_id "
                    "WHERE owner_session_id IS NULL"
                )
            # v2: owner_session_id + created_at composite for the list
            # endpoint's keyset pagination (list_studies ORDER BY
            # created_at DESC WHERE owner_session_id = ?).
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_studies_owner_created "
                "ON studies(owner_session_id, created_at DESC)"
            )
            if "last_traceback" not in cols:
                self._conn.execute(
                    "ALTER TABLE studies ADD COLUMN last_traceback TEXT"
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
            # AEGIS: study_rounds — per-round history for attribution/journal
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS study_rounds (
                    round_id            TEXT PRIMARY KEY,
                    study_id            TEXT NOT NULL,
                    goal_id             TEXT,
                    session_id          TEXT NOT NULL,
                    round_num           INTEGER NOT NULL,
                    run_name            TEXT NOT NULL,
                    metrics_json        TEXT NOT NULL DEFAULT '{}',
                    verdict             TEXT NOT NULL,
                    evidence_ids_json   TEXT NOT NULL DEFAULT '[]',
                    config_changes_json TEXT,
                    agent_output        TEXT,
                    created_at          TEXT NOT NULL,
                    FOREIGN KEY (study_id) REFERENCES studies(study_id) ON DELETE CASCADE
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_rounds_study "
                "ON study_rounds(study_id, round_num)"
            )
            # v2: review section (phase-2 overlay) on round records
            round_cols = [r[1] for r in self._conn.execute("PRAGMA table_info(study_rounds)")]
            if "review_json" not in round_cols:
                self._conn.execute(
                    "ALTER TABLE study_rounds ADD COLUMN review_json TEXT"
                )
            for _col in ("error", "factor_failures_json", "verdict_reason"):
                if _col not in round_cols:
                    self._conn.execute(
                        f"ALTER TABLE study_rounds ADD COLUMN {_col} TEXT"
                    )
            # Release any implicit transaction (migration UPDATE above)
            # so other connections (GoalStore, same DB file) can write.
            self._conn.commit()

    # ── writes ───────────────────────────────────────────────────────

    @synchronized
    def create_study(
        self,
        *,
        owner_session_id: str,
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
        monitor_interval_seconds: int | None = None,
    ) -> StudyRecord:
        """Insert a new study row (status=queued).

        v2 single-identity: ``session_id`` column IS the ``study_id``
        (execution identity + event channel + goal isolation domain); the
        creator's chat session is kept in ``owner_session_id`` for
        ownership lookups and IDOR checks.

        Caller is responsible for creating the goal ledger row (via
        ``GoalStore.replace_goal(session_id=study_id, supersede=False)``)
        and linking it back with ``update_goal_id``.

        ``monitor_interval_seconds``: when set, after the study reaches
        COMPLETE the executor transitions to MONITORING and re-checks
        metric_targets every N seconds. None disables monitoring.
        """

        if not owner_session_id.strip():
            raise ValueError("owner_session_id must not be empty")
        if not objective.strip():
            raise ValueError("objective must not be empty")
        if not workspace_path.strip():
            raise ValueError("workspace_path must not be empty")
        if not strategy_name.strip():
            raise ValueError("strategy_name must not be empty")
        if executor_type not in ("autoresearch", "workflow", "manual"):
                raise ValueError(
                    f"Unknown executor_type: {executor_type!r} "
                    f"(expected 'autoresearch', 'workflow', or 'manual')"
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
            ("monitor_interval_seconds", monitor_interval_seconds),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")

        study_id = new_id("study")
        now = now_iso()
        targets_json = json_dumps(metric_targets or [])
        owner = owner_session_id

        _dlog("store", "create_study id=%s session=%s goal=%s strategy=%s executor=%s",
              study_id, study_id, goal_id, strategy_name, executor_type)
        with write_transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO studies (
                    study_id, session_id, owner_session_id, goal_id, objective,
                    executor_type, workspace_path, strategy_name,
                    metric_targets,
                    budget_token, budget_turn, budget_time_seconds,
                    cooldown_base, cooldown_jitter, min_cooldown,
                    max_rounds, lazy_detection_interval, keep_recent,
                    behavior,
                    execution_status, current_round,
                    heartbeat, created_at, updated_at,
                    monitor_interval_seconds, last_monitor_check_at,
                    monitor_drift_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    study_id,
                    study_id,
                    owner,
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
                    monitor_interval_seconds,
                ),
            )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        logger.info("study created: %s (session=%s goal=%s)", study_id, study_id, goal_id)
        return self._study_from_row(row)

    @synchronized
    def update_goal_id(self, study_id: str, goal_id: str) -> StudyRecord | None:
        """v2: link a study to its goal after the goal ledger row is created.

        The study row is created first (single-identity session_id =
        study_id); the goal is created afterwards with
        ``replace_goal(session_id=study_id, supersede=False)`` and linked
        back here.
        """
        now = now_iso()
        with write_transaction(self._conn):
            self._conn.execute(
                "UPDATE studies SET goal_id = ?, updated_at = ? "
                "WHERE study_id = ?",
                (goal_id, now, study_id),
            )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @synchronized
    def update_execution_status(
        self,
        study_id: str,
        status: StudyStatus,
        *,
        last_error: str | None = None,
        last_traceback: str | None = None,
        last_metrics: dict | None = None,
        last_verdict: str | None = None,
    ) -> StudyRecord | None:
        """Transition a study's execution status.

        Sets ``completed_at`` for terminal statuses; returns the updated
        record (or ``None`` when the study no longer exists).
        """

        _dlog("store", "update_status study=%s → %s error=%s metrics=%s",
              study_id, status.value,
              (last_error or "")[:60],
              "present" if last_metrics else "None")

        now = now_iso()
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
        if last_traceback is not None:
            sets.append("last_traceback = ?")
            params.append(last_traceback)
        if last_metrics is not None:
            sets.append("last_metrics = ?")
            params.append(json_dumps(last_metrics))
        if last_verdict is not None:
            sets.append("last_verdict = ?")
            params.append(last_verdict)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        params.append(study_id)

        with write_transaction(self._conn):
            self._conn.execute(
                f"UPDATE studies SET {', '.join(sets)} WHERE study_id = ?",
                params,
            )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @synchronized
    def update_round_heartbeat(self, study_id: str, current_round: int) -> None:
        """Bump the round counter + heartbeat timestamp (best-effort)."""

        now = now_iso()
        with write_transaction(self._conn):
            self._conn.execute(
                "UPDATE studies SET current_round = ?, heartbeat = ?, updated_at = ? "
                "WHERE study_id = ?",
                (current_round, now, now, study_id),
            )

    @synchronized
    def delete_round(self, study_id: str, round_num: int) -> int:
        """Delete a round's DB row (redo: remove the discarded round)."""
        with write_transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM study_rounds WHERE study_id = ? AND round_num = ?",
                (study_id, round_num),
            )
        return cur.rowcount

    @synchronized
    def update_last_metrics(
        self, study_id: str, metrics: dict, verdict: str
    ) -> None:
        """Record the most recent round's metrics + keep/discard verdict."""

        now = now_iso()
        with write_transaction(self._conn):
            self._conn.execute(
                "UPDATE studies SET last_metrics = ?, last_verdict = ?, "
                "heartbeat = ?, updated_at = ? WHERE study_id = ?",
                (json_dumps(metrics), verdict, now, now, study_id),
            )

    # ── reads ────────────────────────────────────────────────────────

    @synchronized
    def get_study(self, study_id: str) -> StudyRecord | None:
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @synchronized
    def get_active_study(self, session_id: str) -> StudyRecord | None:
        """Return the owner session's currently-running/queued/paused study.

        v2: matched against ``owner_session_id`` (creator chat session) —
        ``session_id`` is the micro session "study:{id}" (event channel).
        """

        placeholders = ",".join("?" for _ in ACTIVE_EXECUTION_STATUSES)
        statuses = [s.value for s in ACTIVE_EXECUTION_STATUSES]
        row = self._conn.execute(
            f"SELECT * FROM studies WHERE owner_session_id = ? "
            f"AND execution_status IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT 1",
            (session_id, *statuses),
        ).fetchone()
        return self._study_from_row(row) if row else None

    @synchronized
    @synchronized
    def list_studies(
        self,
        session_id: str | None = None,
        status: StudyStatus | None = None,
        limit: int = 100,
        before_created_at: str | None = None,
    ) -> list[StudyRecord]:
        """List studies, optionally filtered by owner session; newest first.

        ``before_created_at`` enables keyset pagination (pass the
        ``created_at`` of the last row from the previous page to fetch
        older studies).
        """

        query = "SELECT * FROM studies WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND owner_session_id = ?"
            params.append(session_id)
        if status:
            query += " AND execution_status = ?"
            params.append(status.value)
        if before_created_at:
            query += " AND created_at < ?"
            params.append(before_created_at)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._study_from_row(row) for row in rows]

    @synchronized
    def list_studies_for_owner_sessions(
        self,
        owner_session_ids: list[str],
        status: StudyStatus | None = None,
        limit: int = 100,
        before_created_at: str | None = None,
    ) -> list[StudyRecord]:
        """List studies owned by any of the given owner sessions (newest first).

        Used to scope study listing to a user's sessions (IDOR isolation).
        """
        if not owner_session_ids:
            return []
        placeholders = ",".join("?" for _ in owner_session_ids)
        query = (
            f"SELECT * FROM studies WHERE owner_session_id IN ({placeholders})"
        )
        params: list[Any] = list(owner_session_ids)
        if status:
            query += " AND execution_status = ?"
            params.append(status.value)
        if before_created_at:
            query += " AND created_at < ?"
            params.append(before_created_at)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._study_from_row(row) for row in rows]

    @synchronized
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

    @synchronized
    def delete_session_studies(self, session_id: str) -> int:
        """Delete all study rows owned by a session. Returns the count removed."""

        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        with write_transaction(self._conn):
            row = self._conn.execute(
                "SELECT COUNT(*) FROM studies WHERE owner_session_id = ?",
                (session_id,),
            ).fetchone()
            count = int(row[0]) if row else 0
            # Directives are cascaded via FK ON DELETE CASCADE.
            self._conn.execute(
                "DELETE FROM studies WHERE owner_session_id = ?", (session_id,)
            )
        return count

    # ── directives (Phase 2: mid-execution interaction) ─────────────

    @synchronized
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

        directive_id = new_id("dir")
        now = now_iso()
        with write_transaction(self._conn):
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

    @synchronized
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

    @synchronized
    def list_directives(
        self, study_id: str, limit: int = 50
    ) -> list[StudyDirective]:
        """Return all directives (pending + consumed), newest first."""
        rows = self._conn.execute(
            """
            SELECT directive_id, study_id, content, issued_by, created_at,
                   consumed_at
            FROM study_directives
            WHERE study_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (study_id, limit),
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

    @synchronized
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
        now = now_iso()
        placeholders = ",".join("?" for _ in directive_ids)
        with write_transaction(self._conn):
            cur = self._conn.execute(
                f"UPDATE study_directives "
                f"SET consumed_at = ? "
                f"WHERE study_id = ? AND directive_id IN ({placeholders}) "
                f"AND consumed_at IS NULL",
                [now, study_id, *directive_ids],
            )
        return cur.rowcount

    # ── Phase 3: monitoring hooks ──────────────────────────────────

    @synchronized
    def update_monitor_check(
        self,
        study_id: str,
        *,
        last_check_at: str,
        drift: bool,
    ) -> StudyRecord | None:
        """Update the monitor-check timestamp + drift counter.

        Called by the monitor loop after each periodic check.
        """
        with write_transaction(self._conn):
            if drift:
                self._conn.execute(
                    "UPDATE studies "
                    "SET last_monitor_check_at = ?, "
                    "    monitor_drift_count = monitor_drift_count + 1, "
                    "    updated_at = ? "
                    "WHERE study_id = ?",
                    (last_check_at, last_check_at, study_id),
                )
            else:
                self._conn.execute(
                    "UPDATE studies "
                    "SET last_monitor_check_at = ?, updated_at = ? "
                    "WHERE study_id = ?",
                    (last_check_at, last_check_at, study_id),
                )
        row = self._conn.execute(
            "SELECT * FROM studies WHERE study_id = ?", (study_id,)
        ).fetchone()
        return self._study_from_row(row) if row else None

    @synchronized
    def list_due_for_monitor_check(
        self,
        now_iso: str | None = None,
        limit: int = 100,
    ) -> list[StudyRecord]:
        """List MONITORING studies whose ``last_monitor_check_at`` is older
        than ``monitor_interval_seconds`` (or never checked).

        Used by a background sweeper (Phase 3) — the executor currently
        drives monitoring on its own after COMPLETE.
        """
        # Studies with monitor_interval_seconds IS NOT NULL AND
        # (last_monitor_check_at IS NULL OR last_monitor_check_at <= ? - interval).
        # SQLite has no datetime arithmetic helpers, so we approximate
        # "due" by returning MONITORING studies and letting the executor
        # gate on its own clock. This is cheap enough for Phase 3's
        # small study count.
        rows = self._conn.execute(
            "SELECT * FROM studies "
            "WHERE execution_status = 'monitoring' "
            "  AND monitor_interval_seconds IS NOT NULL "
            "ORDER BY last_monitor_check_at ASC, created_at ASC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._study_from_row(row) for row in rows]

    # ── AEGIS: study_rounds CRUD ──────────────────────────────────────

    @synchronized
    def append_round(
        self,
        study_id: str,
        round_num: int,
        run_name: str,
        metrics: dict | None = None,
        verdict: str = "discard",
        evidence_ids: list[str] | None = None,
        config_changes: dict | None = None,
        agent_output: str | None = None,
        *,
        error: str | None = None,
        factor_failures: list[dict] | None = None,
        verdict_reason: str | None = None,
        review: dict | None = None,
    ) -> "StudyRoundRecord":
        """Append a round record to ``study_rounds``."""
        from .models import StudyRoundRecord
        now = now_iso()
        round_id = new_id("round")
        row = self._conn.execute(
            "SELECT goal_id, session_id FROM studies WHERE study_id = ?",
            (study_id,),
        ).fetchone()
        goal_id = row["goal_id"] if row else None
        session_id = row["session_id"] if row else ""
        with write_transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO study_rounds (
                    round_id, study_id, goal_id, session_id, round_num,
                    run_name, metrics_json, verdict, evidence_ids_json,
                    config_changes_json, agent_output, created_at,
                    review_json, error, factor_failures_json, verdict_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id, study_id, goal_id, session_id, round_num,
                    run_name, json_dumps(metrics or {}), verdict,
                    json_dumps(evidence_ids or []),
                    json_dumps(config_changes) if config_changes else None,
                    agent_output, now,
                    json_dumps(review) if review else None,
                    error,
                    json_dumps(factor_failures) if factor_failures else None,
                    verdict_reason,
                ),
            )
        return StudyRoundRecord(
            round_id=round_id, study_id=study_id, goal_id=goal_id,
            session_id=session_id, round_num=round_num, run_name=run_name,
            metrics=metrics or {}, verdict=verdict,
            evidence_ids=evidence_ids or [], config_changes=config_changes,
            agent_output=agent_output, created_at=now,
            review=review, error=error,
            factor_failures=factor_failures or [],
            verdict_reason=verdict_reason,
        )

    @synchronized
    def update_round(
        self,
        study_id: str,
        round_num: int,
        review: dict,
    ) -> "StudyRoundRecord | None":
        """v2 phase-2 overlay: attach the review section to a round record.

        Complements ``append_round`` (phase-1 body); payload mirrors the
        manifest's ``review`` section (design §9.4/§20.4).
        """
        now = now_iso()
        with write_transaction(self._conn):
            self._conn.execute(
                "UPDATE study_rounds SET review_json = ?, created_at = ? "
                "WHERE study_id = ? AND round_num = ?",
                (json_dumps(review), now, study_id, round_num),
            )
        return self.get_round(study_id, round_num)

    @synchronized
    def list_rounds(
        self, study_id: str, limit: int = 50
    ) -> list["StudyRoundRecord"]:
        """Return round history for a study, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM study_rounds WHERE study_id = ? "
            "ORDER BY round_num DESC LIMIT ?",
            (study_id, limit),
        ).fetchall()
        return [self._round_from_row(r) for r in rows]

    @synchronized
    def count_rounds(self, study_id: str) -> int:
        """Return the true total round count for pagination headers."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM study_rounds WHERE study_id = ?",
            (study_id,),
        ).fetchone()
        return int(row["n"] if row else 0)

    @synchronized
    def get_round(
        self, study_id: str, round_num: int
    ) -> "StudyRoundRecord | None":
        """Return a specific round record."""
        row = self._conn.execute(
            "SELECT * FROM study_rounds WHERE study_id = ? AND round_num = ?",
            (study_id, round_num),
        ).fetchone()
        return self._round_from_row(row) if row else None

    def _round_from_row(self, row: sqlite3.Row) -> "StudyRoundRecord":
        from .models import StudyRoundRecord
        keys = row.keys()
        return StudyRoundRecord(
            round_id=row["round_id"],
            study_id=row["study_id"],
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            round_num=row["round_num"],
            run_name=row["run_name"],
            metrics=json_loads(row["metrics_json"], {}),
            verdict=row["verdict"],
            evidence_ids=json_loads(row["evidence_ids_json"], []),
            config_changes=json_loads(row["config_changes_json"], None),
            agent_output=row["agent_output"],
            review=json_loads(row["review_json"], None)
            if "review_json" in keys else None,
            error=row["error"] if "error" in keys else None,
            factor_failures=json_loads(row["factor_failures_json"], [])
            if "factor_failures_json" in keys else [],
            verdict_reason=row["verdict_reason"] if "verdict_reason" in keys else None,
            created_at=row["created_at"],
        )

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _study_from_row(row: sqlite3.Row) -> StudyRecord:
        """Materialize a ``StudyRecord`` from a SQLite row."""

        targets_raw = json_loads(row["metric_targets"], [])
        # Normalize: tolerate either list[dict] or already-typed shapes.
        if isinstance(targets_raw, list):
            metric_targets = [
                t if isinstance(t, dict) else MetricTarget(**t).as_dict()
                for t in targets_raw
            ]
        else:
            metric_targets = []

        metrics_raw = json_loads(row["last_metrics"], None) if row["last_metrics"] else None

        return StudyRecord(
            study_id=row["study_id"],
            session_id=row["session_id"],
            owner_session_id=row["owner_session_id"] or row["session_id"],
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
            last_traceback=row["last_traceback"],
            heartbeat=row["heartbeat"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            monitor_interval_seconds=row["monitor_interval_seconds"],
            last_monitor_check_at=row["last_monitor_check_at"],
            monitor_drift_count=row["monitor_drift_count"] or 0,
        )
