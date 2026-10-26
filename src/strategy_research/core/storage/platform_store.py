"""PlatformStore —— 数据面唯一写入者（不变量 7）。

依据 docs/stage-3b-plan-20261026.md + platform-data-plane-discussion §9/§10。

存储布局（每 session 一自包含库 + 全局小库）：
    <data_dir>/sessions/<session_id>.sqlite   ← event_log + blobs
    <data_dir>/platform.sqlite               ← session_registry + artifact_registry
                                                + schema_migrations

写入者唯一：所有写操作经由 PlatformStore；其他模块不持有 sqlite 连接。
emit = 同一事务内写 event_log（不变量 7 的物理实现）。
事件永不删除（用户拍板）。月度归档是纯导出，主库不动。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────

_EVENT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS event_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    run_id TEXT,
    schema_v TEXT NOT NULL DEFAULT '1'
)
"""

_BLOBS_DDL = """
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL
)
"""

_SESSION_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS session_registry (
    session_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_active REAL NOT NULL
)
"""

_ARTIFACT_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS artifact_registry (
    artifact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    schema_v TEXT NOT NULL DEFAULT '1',
    status TEXT NOT NULL DEFAULT 'registered',
    created_at REAL NOT NULL,
    created_by_run_id TEXT,
    parent_id TEXT,
    study_id TEXT,
    round_id TEXT
)
"""

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL,
    PRIMARY KEY (domain, version)
)
"""


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    kind: str
    data: dict[str, Any]
    run_id: str | None = None
    schema_v: str = "1"


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    kind: str
    uri: str
    sha256: str
    bytes: int
    schema_v: str = "1"
    status: str = "registered"
    created_at: float = 0.0
    created_by_run_id: str | None = None
    parent_id: str | None = None
    study_id: str | None = None
    round_id: str | None = None


class PlatformStore:
    """数据面唯一写入者（不变量 7）。

    持有全局库连接 + 按需缓存 per-session 库连接（LRU 简化为 dict，A1 改 LRU）。
    emit() 是唯一事件写入入口。store_blob() 是唯一大内容写入入口。
    """

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._sessions_dir = self._data_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._global_path = self._data_dir / "platform.sqlite"
        self._global_conn: sqlite3.Connection | None = None
        self._session_conns: dict[str, sqlite3.Connection] = {}
        self._init_global = False
        self._ensure_global_tables()

    # ── 连接管理 ─────────────────────────────────────────

    def _get_global_conn(self) -> sqlite3.Connection:
        if self._global_conn is None:
            conn = sqlite3.connect(str(self._global_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._global_conn = conn
            self._ensure_global_tables()
        return self._global_conn

    def _get_session_conn(self, session_id: str) -> sqlite3.Connection:
        if session_id not in self._session_conns:
            path = self._sessions_dir / f"{session_id}.sqlite"
            conn = sqlite3.connect(str(path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._ensure_session_tables(conn)
            self._session_conns[session_id] = conn
        return self._session_conns[session_id]

    def _ensure_global_tables(self) -> None:
        conn = self._get_global_conn()
        for ddl in (_SCHEMA_MIGRATIONS_DDL, _SESSION_REGISTRY_DDL, _ARTIFACT_REGISTRY_DDL):
            conn.execute(ddl)
        conn.commit()

    def _ensure_session_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(_EVENT_LOG_DDL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_log_seq ON event_log(seq)"
        )
        conn.execute(_BLOBS_DDL)
        conn.commit()

    # ── P-1 事件流（唯一写入入口） ───────────────────────

    def emit(
        self,
        session_id: str,
        kind: str,
        data: dict[str, Any],
        *,
        run_id: str | None = None,
        schema_v: str = "1",
    ) -> Event:
        """追加事件到 event_log。"""
        conn = self._get_session_conn(session_id)
        ts = time.time()
        cursor = conn.execute(
            "INSERT INTO event_log (ts, kind, data_json, run_id, schema_v) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, kind, json.dumps(data, ensure_ascii=False, default=str),
             run_id, schema_v),
        )
        conn.commit()
        return Event(
            seq=cursor.lastrowid or 0,
            ts=ts,
            kind=kind,
            data=data,
            run_id=run_id,
            schema_v=schema_v,
        )

    def replay(
        self,
        session_id: str,
        *,
        from_seq: int = 0,
        kinds: list[str] | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        """按 session 从指定 seq 起重放事件（fold 用）。"""
        conn = self._get_session_conn(session_id)
        conditions = ["seq > ?"]
        params: list[Any] = [from_seq]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            conditions.append(f"kind IN ({placeholders})")
            params.extend(kinds)
        params.append(limit)
        rows = conn.execute(
            f"SELECT seq, ts, kind, data_json, run_id, schema_v "
            f"FROM event_log WHERE {' AND '.join(conditions)} "
            f"ORDER BY seq LIMIT ?",
            params,
        ).fetchall()
        return [
            Event(seq=r[0], ts=r[1], kind=r[2], data=json.loads(r[3]),
                  run_id=r[4], schema_v=r[5])
            for r in rows
        ]

    def latest_seq(self, session_id: str) -> int:
        conn = self._get_session_conn(session_id)
        row = conn.execute("SELECT MAX(seq) FROM event_log").fetchone()
        return row[0] if row and row[0] is not None else 0

    # ── blob 去重 ────────────────────────────────────────

    def store_blob(self, session_id: str, content: bytes) -> str:
        """内容寻址去重：同哈希不重写，返回哈希。"""
        h = hashlib.sha256(content).hexdigest()
        conn = self._get_session_conn(session_id)
        conn.execute(
            "INSERT OR IGNORE INTO blobs (hash, content, size, created_at) "
            "VALUES (?, ?, ?, ?)",
            (h, content, len(content), time.time()),
        )
        conn.commit()
        return h

    def read_blob(self, session_id: str, hash_: str) -> bytes:
        conn = self._get_session_conn(session_id)
        row = conn.execute(
            "SELECT content FROM blobs WHERE hash = ?", (hash_,)
        ).fetchone()
        if row is None:
            raise KeyError(f"blob {hash_[:12]} not found")
        return row[0]

    # ── 会话注册（全局库） ───────────────────────────────

    def register_session(self, session_key: str) -> str:
        """惰性建会话：返回 session_id（已存在则复用并刷新 last_active）。"""
        conn = self._get_global_conn()
        row = conn.execute(
            "SELECT session_id FROM session_registry WHERE session_key = ?",
            (session_key,),
        ).fetchone()
        now = time.time()
        if row:
            session_id = row[0]
            conn.execute(
                "UPDATE session_registry SET last_active = ? WHERE session_id = ?",
                (now, session_id),
            )
        else:
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO session_registry (session_id, session_key, created_at, last_active) "
                "VALUES (?, ?, ?, ?)",
                (session_id, session_key, now, now),
            )
        conn.commit()
        return session_id

    def list_sessions(self) -> list[dict[str, Any]]:
        conn = self._get_global_conn()
        rows = conn.execute(
            "SELECT session_id, session_key, created_at, last_active "
            "FROM session_registry ORDER BY last_active DESC"
        ).fetchall()
        return [
            {"session_id": r[0], "session_key": r[1],
             "created_at": r[2], "last_active": r[3]}
            for r in rows
        ]

    # ── 工件登记（P-3） ──────────────────────────────────

    def register_artifact(self, artifact: Artifact) -> None:
        conn = self._get_global_conn()
        conn.execute(
            "INSERT OR REPLACE INTO artifact_registry "
            "(artifact_id, kind, uri, sha256, bytes, schema_v, status, "
            " created_at, created_by_run_id, parent_id, study_id, round_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artifact.artifact_id, artifact.kind, artifact.uri,
                artifact.sha256, artifact.bytes, artifact.schema_v,
                artifact.status, artifact.created_at or time.time(),
                artifact.created_by_run_id, artifact.parent_id,
                artifact.study_id, artifact.round_id,
            ),
        )
        conn.commit()

    def list_artifacts(self, study_id: str | None = None) -> list[Artifact]:
        conn = self._get_global_conn()
        if study_id:
            rows = conn.execute(
                "SELECT * FROM artifact_registry WHERE study_id = ? ORDER BY created_at",
                (study_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM artifact_registry ORDER BY created_at"
            ).fetchall()
        return [
            Artifact(
                artifact_id=r[0], kind=r[1], uri=r[2], sha256=r[3],
                bytes=r[4], schema_v=r[5], status=r[6], created_at=r[7],
                created_by_run_id=r[8], parent_id=r[9],
                study_id=r[10], round_id=r[11],
            )
            for r in rows
        ]

    # ── 健康 ─────────────────────────────────────────────

    def quick_check(self) -> bool:
        conn = self._get_global_conn()
        result = conn.execute("PRAGMA quick_check").fetchone()
        return result[0] == "ok" if result else False

    def close(self) -> None:
        for conn in self._session_conns.values():
            conn.close()
        self._session_conns.clear()
        if self._global_conn:
            self._global_conn.close()
            self._global_conn = None
