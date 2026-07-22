"""CommandLoggerHook — 审计日志。

记录所有 hook 事件到 JSONL 文件。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..composite import AgentHook
from ..context import AgentHookContext

logger = logging.getLogger(__name__)


class CommandLoggerHook(AgentHook):
    """审计日志 hook。"""

    name = "command_logger"

    def __init__(self, log_dir: Path | str | None = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else None
        self._log_file: Path | None = None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = self._log_dir / "hooks.log"

    def _log_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """写入日志事件。"""
        if not self._log_file:
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "data": data or {},
        }

        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("Failed to write hook log: %s", e)

    def before_iteration(self, ctx: AgentHookContext) -> None:
        self._log_event("before_iteration", {"iteration": ctx.iteration})

    def after_iteration(self, ctx: AgentHookContext) -> None:
        self._log_event("after_iteration", {"iteration": ctx.iteration})

    def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self._log_event("before_execute_tools", {
            "iteration": ctx.iteration,
            "tool_calls_count": len(ctx.tool_calls),
        })

    def after_tool_executed(
        self, ctx: AgentHookContext, tool_call: Any, result: Any,
    ) -> None:
        tool_name = ""
        if isinstance(tool_call, dict):
            tool_name = tool_call.get("name", "")
        elif hasattr(tool_call, "name"):
            tool_name = tool_call.name

        self._log_event("after_tool_executed", {
            "iteration": ctx.iteration,
            "tool_name": tool_name,
        })

    def on_tool_error(
        self, ctx: AgentHookContext, tool_call: Any, error: BaseException,
    ) -> None:
        self._log_event("on_tool_error", {
            "iteration": ctx.iteration,
            "error": str(error),
        })

    def on_error(self, ctx: AgentHookContext, error: BaseException) -> None:
        self._log_event("on_error", {
            "iteration": ctx.iteration,
            "error": str(error),
        })
