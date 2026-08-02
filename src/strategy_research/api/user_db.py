"""SQLite-backed user store — replaces in-memory dict for persistence.

Usage:
    from strategy_research.api.user_db import get_user_db
    db = get_user_db("/path/to/workspace")
    db.create_user("admin", "Admin", hash_password("admin"))
    user = db.get_user_by_username("admin")
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


class UserDB:
    """Thin wrapper around SQLite for user storage."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        # check_same_thread=False: FastAPI serves requests on a thread
        # pool; a per-instance cached connection must be usable from any
        # worker thread (writes are serialized via self._lock).
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_table(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD ──────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        user_id: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> dict:
        """Create a new user. Returns the user dict."""
        uid = user_id or str(uuid.uuid4())
        ts = created_at or time.time()
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO users (id, username, display_name, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid, username, display_name, password_hash, ts),
            )
            conn.commit()
        return {
            "id": uid,
            "username": username,
            "display_name": display_name,
            "password_hash": password_hash,
            "created_at": ts,
        }

    def update_password(self, user_id: str, new_password_hash: str) -> bool:
        """Set a user's password hash. Returns True if the user exists."""
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_password_hash, user_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_user_by_username(self, username: str) -> Optional[dict]:
        """Look up a user by username, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """Look up a user by id, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def user_count(self) -> int:
        """Number of users in the store."""
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ── Module-level singleton per workspace ───────────────────────

_dbs: dict[str, UserDB] = {}


def get_user_db(workspace_path: Optional[Path] = None) -> UserDB:
    """Get or create a UserDB for the given workspace.

    If workspace_path is None, uses a default location under ~/.quantnodes/.
    """
    if workspace_path is None:
        workspace_path = Path.home() / ".quantnodes"
    workspace_path = Path(workspace_path)
    key = str(workspace_path.resolve())

    if key not in _dbs:
        db_dir = workspace_path
        db_dir.mkdir(parents=True, exist_ok=True)
        _dbs[key] = UserDB(db_dir / "quantnodes_strategy_research_user.db")

    return _dbs[key]


def hash_password(password: str) -> str:
    """SHA-256 password hash (placeholder — upgrade to bcrypt later)."""
    return hashlib.sha256(password.encode()).hexdigest()


def seed_admin_if_empty(db: UserDB) -> None:
    """Insert admin/admin if the users table is empty.

    This runs on every startup. It's idempotent — only inserts if count == 0.
    """
    if db.user_count() == 0:
        db.create_user(
            username="admin",
            display_name="Admin",
            password_hash=hash_password("admin"),
        )
