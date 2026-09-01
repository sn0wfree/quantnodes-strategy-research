"""Checkpoint — Persistent checkpoint storage backend.

Provides SQLite-based checkpoint storage for study state, replacing the
file-only state.json approach. Supports:
- Automatic checkpointing after each round
- Configurable trigger events
- Multiple storage backends (JSON, SQLite)
- Checkpoint recovery and cleanup

Inspired by CrewAI's CheckpointConfig and Temporal's Event History.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CheckpointTrigger(str, Enum):
    """Events that trigger a checkpoint."""
    ROUND_COMPLETED = "round_completed"
    PHASE_COMPLETED = "phase_completed"
    STUDY_PAUSED = "study_paused"
    STUDY_COMPLETED = "study_completed"
    STUDY_ERROR = "study.error"
    CUSTOM = "custom"


@dataclass
class CheckpointConfig:
    """Configuration for checkpoint behavior."""
    location: str = ".checkpoints"  # Directory for JSON or file for SQLite
    on_events: list[CheckpointTrigger] = field(
        default_factory=lambda: [CheckpointTrigger.ROUND_COMPLETED]
    )
    max_checkpoints: int | None = None  # None = keep all
    backend: str = "json"  # "json" or "sqlite"

    def should_trigger(self, event: CheckpointTrigger) -> bool:
        """Check if an event should trigger a checkpoint."""
        return event in self.on_events or CheckpointTrigger.CUSTOM in self.on_events


class CheckpointData:
    """Serialized checkpoint data."""

    def __init__(
        self,
        study_id: str,
        state: dict[str, Any],
        event_seq: int = 0,
        metadata: dict[str, Any] | None = None,
    ):
        self.study_id = study_id
        self.state = state
        self.event_seq = event_seq
        self.metadata = metadata or {}
        self.created_at = time.time()
        # Use UUID to ensure unique IDs even when created in rapid succession
        import uuid
        self.checkpoint_id = f"{study_id}_{int(self.created_at * 1000)}_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "study_id": self.study_id,
            "state": self.state,
            "event_seq": self.event_seq,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckpointData:
        cp = cls(
            study_id=d["study_id"],
            state=d["state"],
            event_seq=d.get("event_seq", 0),
            metadata=d.get("metadata", {}),
        )
        cp.checkpoint_id = d.get("checkpoint_id", cp.checkpoint_id)
        cp.created_at = d.get("created_at", cp.created_at)
        return cp


class CheckpointBackend:
    """Protocol for checkpoint storage backends."""

    def save(self, checkpoint: CheckpointData) -> None:
        """Save a checkpoint."""
        ...

    def load_latest(self, study_id: str) -> CheckpointData | None:
        """Load the latest checkpoint for a study."""
        ...

    def load_all(self, study_id: str) -> list[CheckpointData]:
        """Load all checkpoints for a study."""
        ...

    def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        ...

    def delete_old(self, study_id: str, keep: int) -> int:
        """Delete old checkpoints, keeping only the latest N. Returns count deleted."""
        ...


class JsonCheckpointBackend(CheckpointBackend):
    """JSON file-based checkpoint backend."""

    def __init__(self, location: str):
        self._location = Path(location)
        self._lock = threading.RLock()

    def _get_dir(self, study_id: str) -> Path:
        d = self._location / study_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, checkpoint: CheckpointData) -> None:
        with self._lock:
            d = self._get_dir(checkpoint.study_id)
            p = d / f"{checkpoint.checkpoint_id}.json"
            p.write_text(
                json.dumps(checkpoint.to_dict(), ensure_ascii=False, default=str),
                encoding="utf-8",
            )

    def load_latest(self, study_id: str) -> CheckpointData | None:
        all_cps = self.load_all(study_id)
        return all_cps[-1] if all_cps else None

    def load_all(self, study_id: str) -> list[CheckpointData]:
        with self._lock:
            d = self._get_dir(study_id)
            checkpoints = []
            for p in sorted(d.glob("*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    checkpoints.append(CheckpointData.from_dict(data))
                except (json.JSONDecodeError, OSError):
                    pass
            return checkpoints

    def delete(self, checkpoint_id: str) -> bool:
        with self._lock:
            for p in self._location.rglob(f"{checkpoint_id}.json"):
                p.unlink()
                return True
            return False

    def delete_old(self, study_id: str, keep: int) -> int:
        with self._lock:
            all_cps = self.load_all(study_id)
            if len(all_cps) <= keep:
                return 0
            to_delete = all_cps[:-keep]
            for cp in to_delete:
                self.delete(cp.checkpoint_id)
            return len(to_delete)


class SqliteCheckpointBackend(CheckpointBackend):
    """SQLite-based checkpoint backend."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                study_id TEXT NOT NULL,
                state TEXT NOT NULL,
                event_seq INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoints_study ON checkpoints(study_id)
        """)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def save(self, checkpoint: CheckpointData) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO checkpoints
                       (checkpoint_id, study_id, state, event_seq, metadata, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.study_id,
                        json.dumps(checkpoint.state, ensure_ascii=False, default=str),
                        checkpoint.event_seq,
                        json.dumps(checkpoint.metadata, ensure_ascii=False, default=str),
                        checkpoint.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_latest(self, study_id: str) -> CheckpointData | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT checkpoint_id, study_id, state, event_seq, metadata, created_at
                   FROM checkpoints WHERE study_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (study_id,),
            ).fetchone()
            if row:
                return CheckpointData(
                    study_id=row[1],
                    state=json.loads(row[2]),
                    event_seq=row[3],
                    metadata=json.loads(row[4]),
                )
            return None
        finally:
            conn.close()

    def load_all(self, study_id: str) -> list[CheckpointData]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT checkpoint_id, study_id, state, event_seq, metadata, created_at
                   FROM checkpoints WHERE study_id = ?
                   ORDER BY created_at ASC""",
                (study_id,),
            ).fetchall()
            return [
                CheckpointData(
                    study_id=row[1],
                    state=json.loads(row[2]),
                    event_seq=row[3],
                    metadata=json.loads(row[4]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    def delete(self, checkpoint_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_old(self, study_id: str, keep: int) -> int:
        conn = self._connect()
        try:
            # Get IDs to keep
            rows = conn.execute(
                """SELECT checkpoint_id FROM checkpoints
                   WHERE study_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (study_id, keep),
            ).fetchall()
            keep_ids = {row[0] for row in rows}

            # Delete old ones
            cursor = conn.execute(
                """DELETE FROM checkpoints
                   WHERE study_id = ? AND checkpoint_id NOT IN ({})""".format(
                    ",".join("?" * len(keep_ids))
                ),
                (study_id, *keep_ids) if keep_ids else (study_id,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


class CheckpointManager:
    """Manages checkpoints for studies.

    Provides:
    - Automatic checkpointing based on config
    - Checkpoint recovery
    - Cleanup of old checkpoints
    """

    def __init__(self, config: CheckpointConfig | None = None):
        self._config = config or CheckpointConfig()
        self._backend = self._create_backend()
        self._latest_checkpoints: dict[str, CheckpointData] = {}

    def _create_backend(self) -> CheckpointBackend:
        """Create the appropriate backend based on config."""
        if self._config.backend == "sqlite":
            return SqliteCheckpointBackend(self._config.location)
        else:
            return JsonCheckpointBackend(self._config.location)

    def should_checkpoint(self, event: CheckpointTrigger) -> bool:
        """Check if an event should trigger a checkpoint."""
        return self._config.should_trigger(event)

    def save_checkpoint(
        self,
        study_id: str,
        state: dict[str, Any],
        event_seq: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointData:
        """Save a checkpoint."""
        checkpoint = CheckpointData(
            study_id=study_id,
            state=state,
            event_seq=event_seq,
            metadata=metadata,
        )

        self._backend.save(checkpoint)
        self._latest_checkpoints[study_id] = checkpoint

        # Cleanup old checkpoints if configured
        if self._config.max_checkpoints:
            deleted = self._backend.delete_old(
                study_id, self._config.max_checkpoints,
            )
            if deleted:
                logger.info(
                    "Cleaned up %d old checkpoints for study %s",
                    deleted, study_id,
                )

        logger.debug(
            "Checkpoint saved: %s for study %s",
            checkpoint.checkpoint_id, study_id,
        )
        return checkpoint

    def load_latest(self, study_id: str) -> CheckpointData | None:
        """Load the latest checkpoint for a study."""
        if study_id in self._latest_checkpoints:
            return self._latest_checkpoints[study_id]

        checkpoint = self._backend.load_latest(study_id)
        if checkpoint:
            self._latest_checkpoints[study_id] = checkpoint
        return checkpoint

    def load_all(self, study_id: str) -> list[CheckpointData]:
        """Load all checkpoints for a study."""
        return self._backend.load_all(study_id)

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        return self._backend.delete(checkpoint_id)

    def get_latest_state(self, study_id: str) -> dict[str, Any] | None:
        """Get the latest state from checkpoint."""
        checkpoint = self.load_latest(study_id)
        return checkpoint.state if checkpoint else None


# Global checkpoint manager
_global_manager: CheckpointManager | None = None
_global_lock = threading.Lock()


def get_checkpoint_manager(
    config: CheckpointConfig | None = None,
) -> CheckpointManager:
    """Get or create the global checkpoint manager."""
    global _global_manager
    with _global_lock:
        if _global_manager is None:
            _global_manager = CheckpointManager(config)
        return _global_manager


def reset_checkpoint_manager() -> None:
    """Reset the global checkpoint manager (for testing)."""
    global _global_manager
    with _global_lock:
        _global_manager = None
