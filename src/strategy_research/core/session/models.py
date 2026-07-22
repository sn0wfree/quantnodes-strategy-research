"""Session models — 数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionMessage:
    """会话消息。"""

    role: str  # user/assistant/system
    content: str
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    """会话。"""

    id: str
    created_at: float
    updated_at: float
    workspace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: tuple[SessionMessage, ...] = field(default_factory=tuple)
