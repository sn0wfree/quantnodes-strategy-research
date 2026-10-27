"""ContextHub：唯一上下文组装点（collect → bundle）。

设计：docs/context-hub-discussion.md §6（W0 骨架）；落地记录见
docs/stage-p54-context-hub-w0-design-20261027.md。

本阶段职责边界（与设计稿 W2 的差异如实标注）：
    - 只做 collect + 账本 + 溯源；**不仲裁不裁剪**（W2 ContextPlan 并入）；
    - **不渲染**（bundle → messages 归渲染器：PromptBuilder/ContextBuilder）；
    - provider 故障降级为「无贡献」并记入 bundle.dropped，不外抛（不变式 4）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..utils.token_utils import estimate_tokens as _estimate_message_tokens
from .protocol import ContextItem, ContextProvider, ContextQuery

logger = logging.getLogger(__name__)


def _content_tokens(text: str) -> int:
    """统一 token 口径：复用 token_utils 的消息估算（包成单条 user 消息）。"""
    return _estimate_message_tokens([{"role": "user", "content": text}])


@dataclass
class ContextBundle:
    """一次组装的产出：items + 账本 + 降级记录。

    不变式（讨论稿 §6）：同 query + 同 provider 状态 → 同 bundle（确定性）；
    provider 故障永不外抛。
    """

    items: list[ContextItem] = field(default_factory=list)
    accounting: dict[str, Any] = field(default_factory=dict)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def explain(self) -> dict[str, Any]:
        """本次保留了什么、丢了什么、为什么（调试 agent 行为的第一手材料）。"""
        return {
            "kept": [
                {"kind": i.kind, "provider": i.provider, "source": i.source,
                 "tokens": i.tokens}
                for i in self.items
            ],
            "dropped": list(self.dropped),
            "accounting": dict(self.accounting),
        }


class ContextHub:
    """注册生产方、按 query 收集贡献、产出带账本的 bundle。

    三条硬约束（讨论稿 §3）：只依赖协议（架构门禁钉死 core/context 零
    生产方 import）；不做业务判断（优先级/开关都是 provider 侧声明）；
    不持有会话状态（build 是纯函数式收集）。
    """

    def __init__(self) -> None:
        self._providers: list[ContextProvider] = []

    def register(self, provider: ContextProvider) -> None:
        """注册生产方；同名重复注册是编程错误，直接拒绝。"""
        for existing in self._providers:
            if existing.name == provider.name:
                raise ValueError(f"duplicate context provider: {provider.name!r}")
        self._providers.append(provider)

    def build(self, query: ContextQuery) -> ContextBundle:
        """收集所有注册方的贡献；provider 异常降级为无贡献。

        tokens 由中枢统一估算（provider 不填）；账本按 provider 聚合。
        """
        bundle = ContextBundle()
        per_provider: dict[str, dict[str, int]] = {}
        for provider in self._providers:
            try:
                items = provider.provide(query)
            except Exception as exc:  # noqa: BLE001 — 不变式 4：故障降级
                logger.warning(
                    "context provider %s failed: %s", provider.name, exc,
                )
                bundle.dropped.append({
                    "provider": provider.name, "reason": f"error: {exc}",
                })
                per_provider[provider.name] = {"items": 0, "tokens": 0}
                continue
            owned: list[ContextItem] = []
            for item in items:
                tokens = item.tokens or _content_tokens(item.content)
                owned.append(
                    ContextItem(
                        kind=item.kind,
                        content=item.content,
                        source=item.source,
                        provider=provider.name,
                        priority=item.priority,
                        tokens=tokens,
                        confidence=item.confidence,
                        scope=item.scope,
                        created_at=item.created_at,
                        meta=dict(item.meta),
                    )
                )
            bundle.items.extend(owned)
            per_provider[provider.name] = {
                "items": len(owned),
                "tokens": sum(i.tokens for i in owned),
            }
        total_tokens = sum(i.tokens for i in bundle.items)
        bundle.accounting = {
            "per_provider": per_provider,
            "total_items": len(bundle.items),
            "total_tokens": total_tokens,
        }
        return bundle


__all__ = [
    "ContextBundle",
    "ContextHub",
]
