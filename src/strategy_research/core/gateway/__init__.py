"""gateway —— Gateway 本体（阶段 1a 起步：最小控制器 + fencing + 不变量层）。

落位规则：本包零 FastAPI import、零 core/engine 之外的重依赖。
"""
from __future__ import annotations

from .fencing import FenceToken, FenceVerdict, SessionFence
from .invariants import Invariant, InvariantViolation, invariants, verify_all
from .session_controller import RunResult, SessionController

__all__ = [
    "FenceToken",
    "FenceVerdict",
    "SessionFence",
    "Invariant",
    "InvariantViolation",
    "invariants",
    "verify_all",
    "RunResult",
    "SessionController",
]
