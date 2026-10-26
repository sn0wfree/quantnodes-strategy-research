"""gateway —— Gateway 本体（阶段 1a 起步：最小控制器）。

落位规则：本包零 FastAPI import、零 core/engine 之外的重依赖。
"""
from __future__ import annotations

from .session_controller import RunResult, SessionController

__all__ = ["RunResult", "SessionController"]
