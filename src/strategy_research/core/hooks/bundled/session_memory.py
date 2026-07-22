"""SessionMemoryHook — 自动归档会话到 <workspace>/memory/。

触发时机：
- 用户主动 /reset
- 会话结束
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..composite import AgentHook
from ..context import AgentHookContext

logger = logging.getLogger(__name__)


class SessionMemoryHook(AgentHook):
    """自动归档会话到 <workspace>/memory/。"""

    name = "session_memory"

    def __init__(
        self,
        workspace: Path | str | None = None,
        messages_count: int = 15,
        llm_slug: bool = True,
    ) -> None:
        self._workspace = Path(workspace) if workspace else None
        self._messages_count = messages_count
        self._llm_slug = llm_slug
        self._pending_archive: list[dict[str, Any]] = []

    def after_iteration(self, ctx: AgentHookContext) -> None:
        """收集会话消息用于归档。"""
        if ctx.messages:
            self._pending_archive.extend(ctx.messages[-self._messages_count:])

    def on_error(self, ctx: AgentHookContext, error: BaseException) -> None:
        """错误时也归档。"""
        pass  # 由外部调用 archive_session 触发

    def archive_session(self, session_id: str | None = None) -> Path | None:
        """手动触发归档。"""
        if not self._workspace or not self._pending_archive:
            return None

        memory_dir = self._workspace / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)

        filename = self._generate_filename()
        filepath = memory_dir / filename

        content = self._format_session(session_id, self._pending_archive)
        filepath.write_text(content, encoding="utf-8")

        self._pending_archive.clear()
        logger.info("Session archived to %s", filepath)
        return filepath

    def _generate_filename(self) -> str:
        """生成归档文件名。"""
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        return f"{timestamp}-session.md"

    def _format_session(
        self,
        session_id: str | None,
        messages: list[dict[str, Any]],
    ) -> str:
        """格式化会话为 Markdown。"""
        lines = [
            f"# Session: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
        ]

        if session_id:
            lines.extend([
                "- **Session ID**: " + session_id,
                "",
            ])

        lines.extend([
            "## Conversation Summary",
            "",
        ])

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 截断长消息
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role}: {content}")
            lines.append("")

        return "\n".join(lines)
