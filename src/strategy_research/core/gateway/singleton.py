"""进程内 Gateway 单例（唯一构造点）。

为什么必须唯一（阶段 3a 轻量复查发现的问题）：
session=串行域由 mailbox 保证；若每个入口各持一个 Gateway 实例，
同一 session 经不同入口（API chat / scheduler / CLI）提交会落在
**不同 mailbox**，串行域被打破——两路 turn 并发跑。

因此所有入口一律 `get_gateway()`，共享同一个 EntryPoint 与控制器。
A2 会把这里升级为正式生命周期管理（start/stop、worker 自检、事件总线接线）。
"""
from __future__ import annotations

from typing import Any, Callable

_singleton: dict[str, Any] = {}


def get_gateway(
    *,
    client: Any | None = None,
    system: str | None = None,
    sink: Callable[[str, dict], None] | None = None,
) -> Any:
    """返回进程内唯一 Gateway（首次调用时构造并自检不变量）。

    `client` 仅在首次构造时生效（后续调用忽略，避免半途换 client 造成
    session 状态混乱）。测试可用 `reset_gateway()` 清空。
    """
    gw = _singleton.get("gw")
    if gw is None:
        from .gateway import Gateway

        if client is None:
            from strategy_research.core.llm import LLMConfig, OpenAICompatClient

            client = OpenAICompatClient(LLMConfig.load())
        gw = Gateway(client=client, system=system, sink=sink)
        gw.verify_invariants()  # A5：不变量违反即拒启
        _singleton["gw"] = gw
    return gw


def reset_gateway() -> None:
    """测试用：清空单例（生产不调用）。"""
    _singleton.clear()
