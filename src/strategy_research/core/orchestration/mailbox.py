"""Mailbox（E1 骨架）——per-session 排队域：lane 串行 + steer 桩 + cap + deadline。

依据 docs/entry-controller-contract.md §3/§5。

语义（全部快同步、纯内存；持久化属 0.4 数据面）：
- FOLLOWUP：FIFO 入队。
- INTERRUPT：插队到队首（取消当前 turn 属 A2 准入语义，本骨架只保证排序优先）。
- STEER：v1 仲裁器一律**显式降级为 followup**（注入能力 A4 后启用），
  降级必须发事件（steer.downgraded）——不静默。
- COLLECT：contracts.validate 已在入口拒绝，到不了这里。
- cap（默认 20）溢出 → summarize 策略：最旧两条合并为一条摘要项
  （保持排序位、保留被合并者 id），拒绝点全系统仅此一处之后的入口校验。
- deadline：排队项过期采用惰性判定——每次 submit/poll 驱动 expire_due，
  过期项发 EXPIRED 事件并移除；过期项不计入 cap。

事件经注入的 sink 回调发出（E1 阶段 sink=测试探针；E2 起接 EventStore.emit），
事件类型使用点分命名（与 EventV2 词表风格一致）。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Iterable

from .contracts import (
    DEFAULT_QUEUE_MODE,
    QueueMode,
    Receipt,
    RejectReason,
    WorkKind,
    WorkRequest,
    validate_work_request,
)


@dataclass
class QueueItem:
    """队列项：工单 + 排队元数据。request 不可变；摘要项 content=None。"""

    request: WorkRequest
    enqueued_at: float
    is_summary: bool = False
    summarized_ids: tuple[str, ...] = ()
    interrupt: bool = False

    @property
    def label(self) -> str:
        """审计标签：摘要项给合并数；普通项 = kind + 幂等键/载荷摘要短码。

        载荷摘要短码保证同 kind 的不同工单在事件流里可区分（审计需要）。
        """
        if self.is_summary:
            return f"summary({len(self.summarized_ids)})"
        ident = self.request.idempotency_key or self.request.payload_digest()[:8]
        return f"{self.request.kind.value}:{ident}"


DEFAULT_CAP = 20

EventSink = Callable[[str, dict], None]
SessionResolver = Callable[[str], str]  # session_key -> session_id（惰性创建）


class Mailbox:
    """单个会话的排队域。全局 mailbox 目录由 EntryPoint 持有（按 session 分桶）。"""

    def __init__(
        self,
        session_id: str,
        *,
        cap: int = DEFAULT_CAP,
        clock: Callable[[], float] = time.time,
        sink: EventSink | None = None,
    ) -> None:
        self.session_id = session_id
        self.cap = cap
        self._clock = clock
        self._sink = sink or (lambda kind, data: None)
        self._queue: deque[QueueItem] = deque()

    # ── 入口 ─────────────────────────────────────────────

    def submit(
        self,
        request: WorkRequest,
        *,
        resolve_session: SessionResolver,
    ) -> Receipt:
        """入口校验 + 入队。两级拒绝的第一级在此完成（无副作用返回 rejected）。"""
        reason = validate_work_request(request)
        if reason is not None:
            return Receipt.reject(reason)

        self.expire_due()

        mode = request.queue_mode or DEFAULT_QUEUE_MODE.get(request.source, QueueMode.FOLLOWUP)
        now = self._clock()
        item = QueueItem(
            request=request,
            enqueued_at=now,
            interrupt=(mode is QueueMode.INTERRUPT),
        )

        if mode is QueueMode.STEER:
            # v1 仲裁器：steer 一律显式降级 followup（A4 启用真注入）。
            # 必须发事件——不静默（契约 §2.2 细则 2）。
            self._sink(
                "steer.downgraded",
                {"session_id": self.session_id, "kind": request.kind.value},
            )

        self._evict_expired_locked()  # 先过期再判 cap，避免过期项占位
        self._coalesce_if_full()

        if item.interrupt:
            self._queue.appendleft(item)
        else:
            self._queue.append(item)

        self._sink(
            "work.enqueued",
            {
                "session_id": self.session_id,
                "mode": mode.value,
                "depth": len(self._queue),
                "label": item.label,
            },
        )
        return Receipt.ok(resolve_session(request.session_key))

    # ── 消费者接口（A2 SessionController 使用；E1 由测试驱动） ──

    def poll_due(self, kinds: Iterable[WorkKind] | None = None) -> WorkRequest | None:
        """取下一个可执行工单：interrupt 优先，其后 FIFO；过期项在此惰性出清。

        kinds：可选的 kind 过滤（消费者只领自己负责的工单）。不匹配的工单
        **留在队列**，不丢弃；interrupt 优先级在过滤后的集合内生效。
        """
        self.expire_due()
        if not self._queue:
            return None
        if kinds is None:
            return self._pop_next().request
        allowed = {k if isinstance(k, WorkKind) else WorkKind(k) for k in kinds}
        item = self._pop_next_where(lambda it: it.request.kind in allowed)
        return item.request if item is not None else None

    def peek_depth(self, kinds: Iterable[WorkKind] | None = None) -> int:
        self.expire_due()
        if kinds is None:
            return len(self._queue)
        allowed = {k if isinstance(k, WorkKind) else WorkKind(k) for k in kinds}
        return sum(1 for it in self._queue if it.request.kind in allowed)

    def snapshot(self) -> Iterable[QueueItem]:
        """只读快照（status/fold 用）；返回前先出清过期。"""
        self.expire_due()
        return tuple(self._queue)

    # ── deadline ─────────────────────────────────────────

    def expire_due(self, now: datetime | None = None) -> int:
        """出清所有已过 deadline 的排队项，发 EXPIRED 事件。返回清理数。"""
        now_ts = self._clock()
        expired: list[QueueItem] = []
        remaining: deque[QueueItem] = deque()
        for item in self._queue:
            dl = item.request.deadline
            if dl is not None and dl.timestamp() <= now_ts:
                expired.append(item)
            else:
                remaining.append(item)
        if not expired:
            return 0
        self._queue = remaining
        for item in expired:
            self._sink(
                "work.expired",
                {
                    "session_id": self.session_id,
                    "label": item.label,
                    "deadline": item.request.deadline.isoformat()
                    if item.request.deadline
                    else None,
                },
            )
        return len(expired)

    # ── 内部 ─────────────────────────────────────────────

    def _evict_expired_locked(self) -> None:
        self.expire_due()

    def _coalesce_if_full(self) -> None:
        """cap 溢出 → summarize：最旧两条合并为一条摘要项（排序位保持）。"""
        while len(self._queue) >= self.cap:
            oldest = self._queue.popleft()
            second = self._queue.popleft() if self._queue else None
            if second is None:
                # 只剩一条也满（cap==1 的极端）：直接摘要它自己
                merged_ids = (oldest.label,)
                merged = QueueItem(
                    request=oldest.request,
                    enqueued_at=oldest.enqueued_at,
                    is_summary=True,
                    summarized_ids=merged_ids,
                )
                self._queue.appendleft(merged)
            else:
                merged_ids = (oldest.label, second.label)
                if oldest.is_summary:
                    merged_ids = oldest.summarized_ids + (
                        second.label,
                    )
                merged = QueueItem(
                    request=second.request,
                    enqueued_at=oldest.enqueued_at,
                    is_summary=True,
                    summarized_ids=merged_ids,
                )
                self._queue.appendleft(merged)
            self._sink(
                "work.summarized",
                {"session_id": self.session_id, "merged": list(merged_ids)},
            )

    def _pop_next(self) -> QueueItem:
        """interrupt 优先，其后 FIFO。"""
        return self._pop_next_where(lambda item: True)  # type: ignore[return-value]

    def _pop_next_where(self, predicate: Callable[[QueueItem], bool]) -> QueueItem | None:
        """在满足 predicate 的项中取：interrupt 优先，其后 FIFO。

        不满足 predicate 的项原位保留（kind 过滤消费用完即还）。
        """
        for idx, item in enumerate(self._queue):
            if item.interrupt and not item.is_summary and predicate(item):
                del self._queue[idx]
                return item
        for idx, item in enumerate(self._queue):
            if predicate(item):
                del self._queue[idx]
                return item
        return None
