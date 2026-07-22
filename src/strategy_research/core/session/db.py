"""SessionDB — SQLite 管理。

使用触发器自动同步 FTS5 索引，无需应用层手动维护。
支持写入限流和监控。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from .models import Session, SessionMessage
from .rate_limiter import RateLimiter
from .metrics import MetricsLogger

logger = logging.getLogger(__name__)

SESSIONS_DB = Path.home() / ".quantnodes-research" / "sessions.db"

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at REAL,
    updated_at REAL,
    workspace TEXT,
    metadata_json TEXT
);
"""

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    role TEXT,
    content TEXT,
    timestamp REAL,
    metadata_json TEXT
);
"""

_CREATE_MESSAGES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);
"""

# 触发器：自动同步 FTS5 索引
_CREATE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) 
    VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) 
    VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionDB:
    """SQLite 会话存储。"""

    def __init__(
        self,
        db_path: Path | str | None = None,
        rate_limiter: RateLimiter | None = None,
        metrics_logger: MetricsLogger | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path else SESSIONS_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._metrics_logger = metrics_logger or MetricsLogger()
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表和触发器。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(_CREATE_SESSIONS)
            conn.execute(_CREATE_MESSAGES)
            conn.execute(_CREATE_MESSAGES_FTS)
            conn.executescript(_CREATE_TRIGGERS)

    def create_session(
        self,
        session_id: str,
        workspace: str = "",
        metadata: dict | None = None,
    ) -> Session:
        """创建新会话。"""
        now = time.time()
        metadata_json = json.dumps(metadata or {})

        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "INSERT INTO sessions (id, created_at, updated_at, workspace, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, now, now, workspace, metadata_json),
            )

        return Session(
            id=session_id,
            created_at=now,
            updated_at=now,
            workspace=workspace,
            metadata=metadata or {},
        )

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return Session(
                id=row["id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                workspace=row["workspace"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )

    def list_sessions(
        self,
        workspace: str | None = None,
        limit: int = 50,
    ) -> List[Session]:
        """列出会话。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if workspace:
                cursor = conn.execute(
                    "SELECT * FROM sessions WHERE workspace = ? ORDER BY updated_at DESC LIMIT ?",
                    (workspace, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            return [
                Session(
                    id=row["id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    workspace=row["workspace"],
                    metadata=json.loads(row["metadata_json"] or "{}"),
                )
                for row in cursor
            ]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> SessionMessage:
        """添加消息。"""
        now = time.time()
        metadata_json = json.dumps(metadata or {})

        # 限流
        wait_time = self._rate_limiter.acquire(1)
        if wait_time > 0:
            time.sleep(wait_time)

        start = time.time()
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, timestamp, metadata_json) VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, content, now, metadata_json),
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            duration = time.time() - start
            self._metrics_logger.record_write(1, duration, True)
        except Exception as e:
            duration = time.time() - start
            self._metrics_logger.record_write(1, duration, False, error=str(e))
            raise

        return SessionMessage(
            role=role,
            content=content,
            timestamp=now,
            metadata=metadata or {},
        )

    def get_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[SessionMessage]:
        """获取会话消息。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            )
            messages = [
                SessionMessage(
                    role=row["role"],
                    content=row["content"],
                    timestamp=row["timestamp"],
                    metadata=json.loads(row["metadata_json"] or "{}"),
                )
                for row in cursor
            ]
            messages.reverse()
            return messages

    def search_messages(
        self,
        query: str,
        limit: int = 20,
    ) -> List[dict]:
        """FTS5 全文搜索消息。"""
        if not query.strip():
            return []

        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT m.session_id, m.role, m.content, m.timestamp,
                           fts.rank AS score
                    FROM messages_fts fts
                    JOIN messages m ON m.id = fts.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY fts.rank
                    LIMIT ?
                    """,
                    (query, limit),
                )
                return [dict(row) for row in cursor]
        except Exception as e:
            logger.warning("FTS5 search failed: %s", e)
            return []

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其消息。"""
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def add_message_batch(
        self,
        session_id: str,
        messages: list[dict],
    ) -> int:
        """批量添加消息（带限流）。

        Args:
            session_id: 会话 ID。
            messages: [{"role": str, "content": str, "metadata": dict}]

        Returns:
            成功插入的消息数。
        """
        if not messages:
            return 0

        now = time.time()

        # 限流
        wait_time = self._rate_limiter.acquire(len(messages))
        if wait_time > 0:
            time.sleep(wait_time)

        start = time.time()
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.executemany(
                    "INSERT INTO messages (session_id, role, content, timestamp, metadata_json) VALUES (?, ?, ?, ?, ?)",
                    [
                        (session_id, m.get("role", "user"), m.get("content", ""), now, json.dumps(m.get("metadata", {})))
                        for m in messages
                    ],
                )
                conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
            duration = time.time() - start
            self._metrics_logger.record_write(len(messages), duration, True)
            return len(messages)
        except Exception as e:
            duration = time.time() - start
            self._metrics_logger.record_write(len(messages), duration, False, error=str(e))
            raise

    @property
    def rate_limiter(self) -> RateLimiter:
        """获取限流器。"""
        return self._rate_limiter

    @property
    def metrics_logger(self) -> MetricsLogger:
        """获取指标记录器。"""
        return self._metrics_logger
