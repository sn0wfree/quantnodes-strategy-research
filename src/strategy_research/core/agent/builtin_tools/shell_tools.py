"""Shell execution tool — run arbitrary commands in the workspace.

Gated by ``allow_shell_tools``; not registered by default in
``build_default_registry()``.  Must be explicitly opted-in via
``register_shell_tools(registry)``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

from ..tools import BaseTool, ToolRegistry
from .utils import err_actionable, safe_get_param

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT = 30  # seconds
_MAX_OUTPUT_CHARS = 50_000  # truncate stdout/stderr to keep LLM context small
# All patterns are pre-lowercased so the in-string check against
# command.lower() is case-insensitive.
_BLOCKED_COMMANDS = frozenset({
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/sd",
    "chmod -r 777 /",
})


# ── Helper ──────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, total {len(text)} chars)"


# ── Tool ────────────────────────────────────────────────────────────


class ShellExecTool(BaseTool):
    """Execute a shell command in the workspace directory."""

    name = "run_command"
    description = (
        "Execute a shell command in the workspace directory. "
        "Returns stdout, stderr, and exit code. Use this for installing "
        "packages (pip install), checking environment (python -c), "
        "running scripts, git operations, or any system command. "
        "Commands run with a timeout and output is truncated for safety."
    )
    parameters = {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace root path (command runs in this directory).",
            },
            "command": {
                "type": "string",
                "description": "Shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30, max 120).",
            },
        },
        "required": ["workspace", "command"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        from pathlib import Path

        # ── Parse workspace ──────────────────────────────────
        ws_raw = kwargs.get("workspace")
        if ws_raw is None:
            return err_actionable(
                "missing required kwarg 'workspace'",
                expected="absolute path to workspace root",
                fix="pass workspace='/path/to/your/workspace'",
                tool="run_command",
            )
        workspace = Path(str(ws_raw)).resolve()

        # ── Parse command ────────────────────────────────────
        command = kwargs.get("command")
        if not command or not isinstance(command, str):
            return err_actionable(
                "missing or empty 'command' parameter",
                expected="a valid shell command string, e.g. 'pip install pandas'",
                fix="pass command='your shell command here'",
                tool="run_command",
            )

        # ── Parse timeout ────────────────────────────────────
        try:
            timeout = safe_get_param(kwargs, "timeout", int, default=_DEFAULT_TIMEOUT)
        except TypeError:
            timeout = _DEFAULT_TIMEOUT
        timeout = max(1, min(timeout, 120))  # clamp 1..120

        # ── Safety: block obviously destructive commands ──────
        cmd_lower = command.lower().strip()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return err_actionable(
                    f"command blocked for safety: contains '{blocked}'",
                    received=command,
                    fix="use a less destructive command",
                    tool="run_command",
                )

        # ── Execute ──────────────────────────────────────────
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            elapsed = round(time.monotonic() - t0, 2)
        except subprocess.TimeoutExpired:
            return err_actionable(
                f"command timed out after {timeout}s",
                received=command,
                fix="increase timeout or use a shorter-running command",
                tool="run_command",
            )
        except FileNotFoundError:
            return err_actionable(
                "shell not found (sh/bash not in PATH)",
                received=command,
                fix="check that /bin/sh or /bin/bash exists",
                tool="run_command",
            )
        except Exception as exc:
            return err_actionable(
                f"command execution failed: {exc}",
                received=command,
                fix="verify the command syntax is correct",
                tool="run_command",
            )

        stdout = _truncate(result.stdout)
        stderr = _truncate(result.stderr)

        return json.dumps({
            "status": "ok",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "elapsed_seconds": elapsed,
            "command": command,
        }, ensure_ascii=False)


# ── Registration ────────────────────────────────────────────────────


def register_shell_tools(registry: ToolRegistry) -> None:
    """Register shell tools into the given registry."""
    registry.register(ShellExecTool())
