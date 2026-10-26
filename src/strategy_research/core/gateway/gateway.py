"""Gateway 组装（阶段 3a）—— 单进程内核的可运行形态。

负责：
- 构造 EntryPoint（含 sink → EventStore/SSE）
- 构造 SessionController（持有 client、引擎工厂、fence）
- 提供 `run_once()` 驱动队列（同步；A2 的后台消费者替换此调用方式）
- 不 import FastAPI（落位规则 INV：gateway 零 FastAPI）

这一层是「挂载式单进程」的内核：uvicorn/CLI/MCP 都只是它的宿主。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from strategy_research.core.gateway.fencing import SessionFence
from strategy_research.core.gateway.session_controller import RunResult, SessionController
from strategy_research.core.orchestration import EntryPoint

logger = logging.getLogger(__name__)


class Gateway:
    """单进程内核：入口 + 控制器 + fence +（后续）事件存储。"""

    def __init__(
        self,
        *,
        client: Any,
        system: str | None = None,
        sink: Callable[[str, dict], None] | None = None,
        engine_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._events: list[tuple[str, dict]] = []
        self._external_sink = sink
        # 内部缓冲与外部 sink 同时工作：drain_events() 永远可用，
        # 外部 sink 收到同样的事件（审计门② 的性能基线也依赖这条）。
        combined = self._combined_sink
        self.entry = EntryPoint(sink=combined)
        self.fence = SessionFence()
        self.controller = SessionController(
            self.entry, client=client, system=system, sink=combined,
        )
        if engine_factory is not None:
            self.controller._build_engine = engine_factory  # A1 前的最小注入点

    def _combined_sink(self, kind: str, data: dict) -> None:
        self._default_sink(kind, data)
        if self._external_sink is not None:
            self._external_sink(kind, data)

    def _default_sink(self, kind: str, data: dict) -> None:
        self._events.append((kind, data))
        logger.debug("event %s %s", kind, data)

    # ── 驱动 ─────────────────────────────────────────────

    def run_once(self) -> list[RunResult]:
        """处理当前所有排队工单（同步）。A2 换成常驻消费者。"""
        return self.controller.run_pending()

    # ── 观测 ─────────────────────────────────────────────

    def drain_events(self) -> list[tuple[str, dict]]:
        events, self._events = self._events, []
        return events

    def verify_invariants(self) -> None:
        """启动自检（A5）：不变量违反即拒启。"""
        from strategy_research.core.gateway.invariants import verify_all

        verify_all()
