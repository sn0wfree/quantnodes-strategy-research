"""Gateway 组装（阶段 3b）—— 单进程内核的可运行形态。

阶段 3b 升级：
- engine_factory 从 monkey-patch 升级为构造参数
- 集成 PlatformStore（事件持久化 + blob + artifact 登记）
- 灰度 flag 默认开（旧路径保留但 deprecated）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from strategy_research.core.gateway.fencing import SessionFence
from strategy_research.core.gateway.session_controller import RunResult, SessionController
from strategy_research.core.orchestration import EntryPoint

logger = logging.getLogger(__name__)


class Gateway:
    """单进程内核：入口 + 控制器 + fence + 事件存储 + 不变量自检。"""

    def __init__(
        self,
        *,
        client: Any,
        system: str | None = None,
        sink: Callable[[str, dict], None] | None = None,
        engine_factory: Callable[[], Any] | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._events: list[tuple[str, dict]] = []
        self._external_sink = sink
        combined = self._combined_sink
        self.entry = EntryPoint(sink=combined)
        self.fence = SessionFence()
        self.controller = SessionController(
            self.entry, client=client, system=system, sink=combined,
            engine_factory=engine_factory,
        )
        # 阶段 3b：PlatformStore（事件持久化 + blob + artifact 登记）
        self._store = None
        if data_dir is not None:
            from strategy_research.core.storage.platform_store import PlatformStore

            self._store = PlatformStore(data_dir=data_dir)
        # 保持兼容：_build_engine 仍可访问（A1 preset 注册表会替换）

    @property
    def store(self):
        """PlatformStore（可能为 None——data_dir 未配置时纯内存模式）。"""
        return self._store

    def _combined_sink(self, kind: str, data: dict) -> None:
        self._default_sink(kind, data)
        if self._external_sink is not None:
            self._external_sink(kind, data)

    def _default_sink(self, kind: str, data: dict) -> None:
        self._events.append((kind, data))
        # 阶段 3b：事件持久化到 PlatformStore（如果配置了）
        if self._store is not None:
            session_id = data.get("session_id", "default")
            try:
                self._store.emit(session_id, kind, data)
            except Exception:  # noqa: BLE001
                logger.debug("event persistence failed: %s", kind, exc_info=True)
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
        from strategy_research.core.gateway.invariants import verify_all

        verify_all()

    def close(self) -> None:
        if self._store:
            self._store.close()
