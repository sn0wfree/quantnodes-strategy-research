"""SessionManager — 会话管理。"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from .db import SessionDB
from .models import Session, SessionMessage

logger = logging.getLogger(__name__)


class SessionManager:
    """会话管理器。"""

    def __init__(self, db: SessionDB | None = None) -> None:
        self._db = db or SessionDB()

    def create_session(
        self,
        session_id: str,
        workspace: str = "",
        metadata: dict | None = None,
    ) -> Session:
        """创建新会话。"""
        return self._db.create_session(session_id, workspace, metadata)

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话。"""
        return self._db.get_session(session_id)

    def list_sessions(
        self,
        workspace: str | None = None,
        limit: int = 50,
    ) -> List[Session]:
        """列出会话。"""
        return self._db.list_sessions(workspace, limit)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> SessionMessage:
        """添加消息。"""
        return self._db.add_message(session_id, role, content, metadata)

    def get_messages(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[SessionMessage]:
        """获取会话消息。"""
        return self._db.get_messages(session_id, limit)

    def search_messages(
        self,
        query: str,
        limit: int = 20,
    ) -> List[dict]:
        """FTS5 全文搜索消息。"""
        return self._db.search_messages(query, limit)

    def delete_session(self, session_id: str) -> bool:
        """删除会话。"""
        return self._db.delete_session(session_id)

    def archive_session(self, session_id: str, messages_count: int = 15) -> str | None:
        """归档会话到 memory 目录。"""
        messages = self.get_messages(session_id, limit=messages_count)
        if not messages:
            return None

        # 格式化为 Markdown
        lines = [f"# Session: {session_id}", ""]
        for msg in messages:
            lines.append(f"{msg.role}: {msg.content[:500]}")
            lines.append("")

        return "\n".join(lines)
