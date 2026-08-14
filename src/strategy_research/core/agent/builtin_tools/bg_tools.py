"""Background command tool — single entry ``run_bg_command``.

Manages nohup-style background tasks for agents: start / status / wait
/ log / kill via one tool with an ``action`` parameter (design
``docs/study-long-task-background-plan.md`` §5).

The task registry lives in ``core/utils/bg_proc.py`` (shared with
``RunBacktestTool(background=True)`` and ``backtest.run_strategy``), so
a task started anywhere is pollable here by ``task_id`` — and watchdogs
/ round-end harvesters sweep the same registry.

Opt-in tool (mirrors shell tools): register via
``register_bg_tools(registry)``; roles get it through the tool
whitelist in ``role_factory``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ...utils.bg_proc import (
    BgTaskHandle,
    active_tasks,
    get_task,
    is_stalled,
    kill_bg,
    log_progress,
    log_tail,
    register_task,
    register_thread_task,
    set_task_result,
    unregister_task,
)
from ..tools import EFFECT_FS, EFFECT_NET, BaseTool, ToolContext, ToolRegistry
from .shell_tools import _BLOCKED_COMMANDS

logger = logging.getLogger(__name__)

_STALL_TIMEOUT = 300.0  # seconds without log progress → stalled
_MAX_WAIT_SECONDS = 120  # clamp for the wait observation window

__all__ = [
    "RunBgCommandTool",
    "register_bg_tools",
    "active_tasks",
    "get_task",
    "log_progress",
    "register_task",
    "register_thread_task",
    "set_task_result",
    "unregister_task",
]


def _status_payload(task_id: str, handle: BgTaskHandle) -> dict:
    if not handle.is_alive():
        payload: dict[str, Any] = {
            "task_id": task_id, "state": "done",
            "log": str(handle.log_path),
        }
        if handle.proc is not None:
            payload["exit_code"] = handle.proc.returncode
        if handle.result is not None:
            payload["result"] = handle.result
        return payload
    if is_stalled(handle.log_path, _STALL_TIMEOUT):
        return {
            "task_id": task_id, "state": "stalled",
            "stalled_seconds": int(time.time() - handle.log_path.stat().st_mtime)
            if handle.log_path.exists() else None,
            "log": str(handle.log_path),
            "tail": log_tail(handle.log_path, n=3),
        }
    return {
        "task_id": task_id, "state": "running",
        "log": str(handle.log_path),
        "tail": log_tail(handle.log_path, n=3),
    }


# ── Tool ────────────────────────────────────────────────────────────


class RunBgCommandTool(BaseTool):
    """后台任务管理（单入口，action 分派）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.0.0
    #
    # ## 用途
    # 将耗时长命令转为后台执行（nohup 语义），日志持续落盘 run.log；
    # 通过日志推进判定进展（写日志 = 正常，停滞 > 300s = 卡死）。
    # 长任务（长回测/大数据计算/下载）用此工具，避免阻塞当前回合。
    #
    # ## action 参数
    # - start:  后台启动 command（log 可选，默认 workspace/bg_tasks/<id>.log）
    #   → {task_id, log}
    # - status: 查询任务状态 → running|stalled|done（含 exit_code/停滞秒数/尾部3行）
    # - wait:   观察窗（内部等待 seconds 秒，1..120）→ status
    # - log:    读日志尾部 n_lines 行（默认 20）
    # - kill:   终止任务（整组 kill）并注销
    #
    # ## 轮询协议（配合 _common/rules/long-task.md）
    # 预判长任务 → start 或 run_backtest(background=True)
    #   → wait(task_id, 15) 观察窗 × 最多 3 次
    #   → 有新日志行 = 进行中；3 次无进展 = 停滞，停止轮询交由系统 watchdog
    #   → 完成（exit_code=0）→ 读结果文件继续
    #
    # ## 约束
    # - 同一 task_id 只操作一次（不重复启动）
    # - 每次 log 读取 ≤ n_lines（默认 20 行），控制 token 成本
    # ─────────────────────────────────────────────
    """

    name = "run_bg_command"
    description = (
        "后台任务管理：start/status/wait/log/kill（长命令转后台 + 日志轮询）。"
    )
    repeatable = True
    category = "后台任务"
    effects = frozenset({EFFECT_FS, EFFECT_NET})

    def execute(
        self,
        ctx: ToolContext,
        action: str,
        task_id: str = "",
        command: str = "",
        cwd: str = "",
        log: str = "",
        seconds: int = 15,
        n_lines: int = 20,
    ) -> str:
        workspace = ctx.workspace
        if workspace is None:
            return json.dumps({
                "status": "error",
                "error": "missing workspace context",
                "fix": "AgentLoop 注入 workspace",
            }, ensure_ascii=False)

        if action not in ("start", "status", "wait", "log", "kill"):
            return json.dumps({
                "status": "error",
                "error": f"unknown action: {action}",
                "expected": "start|status|wait|log|kill",
            }, ensure_ascii=False)

        try:
            if action == "start":
                return self._start(ctx, command, cwd, log)
            handle = get_task(task_id)
            if handle is None:
                return json.dumps({
                    "status": "error",
                    "error": f"unknown task_id: {task_id}",
                    "fix": "先 start 或 run_backtest(background=True) 获取 task_id",
                }, ensure_ascii=False)
            if action == "status":
                return json.dumps(_status_payload(task_id, handle), ensure_ascii=False)
            if action == "wait":
                wait = max(1, min(int(seconds), _MAX_WAIT_SECONDS))
                time.sleep(wait)
                return json.dumps(_status_payload(task_id, handle), ensure_ascii=False)
            if action == "log":
                n = max(1, min(int(n_lines), 200))
                return json.dumps({
                    "task_id": task_id,
                    "log": log_tail(handle.log_path, n=n),
                }, ensure_ascii=False)
            # kill
            if handle.proc is not None:
                kill_bg(handle.proc)
            else:
                # thread-mode tasks can't be force-killed; deregistering
                # makes them invisible (the thread finishes on its own)
                logger.warning(
                    "run_bg_command kill: thread task %s not force-killable; "
                    "deregistered", task_id,
                )
            unregister_task(task_id)
            return json.dumps({
                "status": "ok", "task_id": task_id, "killed": True,
            }, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_bg_command %s failed", action)
            return json.dumps({
                "status": "error", "error": f"{exc}",
            }, ensure_ascii=False)

    def _start(self, ctx: ToolContext, command: str, cwd: str, log: str) -> str:
        if not command or not isinstance(command, str):
            return json.dumps({
                "status": "error",
                "error": "missing or empty 'command'",
                "expected": "a valid shell command string",
            }, ensure_ascii=False)
        cmd_lower = command.lower().strip()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return json.dumps({
                    "status": "error",
                    "error": f"command blocked for safety: contains '{blocked}'",
                }, ensure_ascii=False)

        from ...utils.bg_proc import run_bg

        ws = Path(str(ctx.workspace)).resolve()
        workdir = Path(cwd).resolve() if cwd else ws
        log_path = Path(log).resolve() if log else \
            ws / "bg_tasks" / f"{uuid.uuid4().hex[:8]}.log"
        proc = run_bg(["bash", "-lc", command], log_path, cwd=workdir)
        task_id = register_task(proc, log_path, command)
        return json.dumps({
            "status": "running", "task_id": task_id,
            "log": str(log_path), "pid": proc.pid,
        }, ensure_ascii=False)


def register_bg_tools(registry: ToolRegistry) -> None:
    """Register background-command tools into the given registry."""
    registry.register(RunBgCommandTool())
