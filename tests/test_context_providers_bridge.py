"""PreRunContextProvider 桥接等价测试。

方法论（对齐 test_research_preset）：同一 loop 状态分别喂旧 injector
叠加循环与 hub 渲染路径，断言 full_task 逐字节相等——含检索 query 的
链式语义（memory 检索用 goal 拼接后的串）。
"""
from __future__ import annotations

from types import SimpleNamespace

from strategy_research.core.agent.context_providers import (
    build_prerun_context_provider,
)
from strategy_research.core.context import ContextHub, ContextQuery

TASK = "improve momentum_20_60"
GOAL_BLOCK = "<current-research-goal>\n目标是提升 Calmar\n</current-research-goal>"
MEMORY_BLOCK = "<relevant-memory>\n- [memory/笔记] 上轮动量策略已尝试\n</relevant-memory>"


def _make_injectors(captured: dict):
    """两个 pre-run injector 假件：goal(-100) 先拼、memory(-90) 用拼接串检索。"""

    class GoalInjector:
        name = "goal_context"
        order = -100

        def inject_pre_run(self, loop, task, messages):
            if not loop.enable_goal_injection:
                return task
            captured["goal_query"] = task  # goal 收到原始 task
            return GOAL_BLOCK + "\n\n" + task

    class MemoryInjector:
        name = "memory_retriever"
        order = -90

        def inject_pre_run(self, loop, task, messages):
            if not loop.enable_memory_injection:
                return task
            captured["memory_query"] = task  # 链式语义：含 goal 前缀
            return MEMORY_BLOCK + "\n\n" + task

    return [GoalInjector(), MemoryInjector()]


def _make_loop(**flags):
    return SimpleNamespace(
        enable_goal_injection=flags.get("enable_goal_injection", True),
        enable_memory_injection=flags.get("enable_memory_injection", True),
    )


def _legacy_prerun(loop, injectors, task):
    """旧 _prepare_run 的 pre-run 循环（参照实现）。"""
    for injector in sorted(injectors, key=lambda i: i.order):
        if getattr(injector, "order", 0) < 0:
            try:
                task = injector.inject_pre_run(loop, task, [])
            except Exception:
                pass
    return task


def _hub_prerun(loop, injectors, task):
    """新路径：整链桥接 → hub.build → 渲染。"""
    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=task))
    if bundle.items:
        return (
            "\n\n".join(item.content for item in bundle.items)
            + "\n\n" + task
        )
    return task


def test_full_task_byte_identical_to_legacy_loop():
    captured: dict = {}
    injectors = _make_injectors(captured)
    loop = _make_loop()

    legacy = _legacy_prerun(loop, injectors, TASK)
    bridged = _hub_prerun(loop, injectors, TASK)

    assert legacy == bridged == (
        MEMORY_BLOCK + "\n\n" + GOAL_BLOCK + "\n\n" + TASK
    )


def test_memory_retrieval_query_keeps_chained_semantics():
    """检索 query 是 goal 拼接后的串（整链桥接的核心等价点）。"""
    captured: dict = {}
    injectors = _make_injectors(captured)
    loop = _make_loop()

    _hub_prerun(loop, injectors, TASK)

    assert captured["goal_query"] == TASK
    assert captured["memory_query"] == GOAL_BLOCK + "\n\n" + TASK


def test_rendered_items_are_sourced_by_kind():
    """items 按最终渲染序排列，kind/source 可溯源。"""
    injectors = _make_injectors({})
    loop = _make_loop()

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert [(i.kind, i.source) for i in bundle.items] == [
        ("memory", "injector:memory_retriever"),
        ("goal", "injector:goal_context"),
    ]
    assert bundle.items[0].content == MEMORY_BLOCK
    assert bundle.items[1].content == GOAL_BLOCK


def test_disabled_flags_yield_no_items():
    captured: dict = {}
    injectors = _make_injectors(captured)

    hub = ContextHub()
    hub.register(build_prerun_context_provider(
        _make_loop(enable_goal_injection=False, enable_memory_injection=False),
        injectors,
    ))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert bundle.items == []


def test_injector_exception_degrades_like_legacy_pass():
    """单个 injector 抛异常：旧循环是 pass（跳过该 injector，链继续），
    桥接同语义——后续 injector 照常执行。"""
    class ExplodingInjector:
        name = "goal_context"
        order = -100

        def inject_pre_run(self, loop, task, messages):
            raise RuntimeError("goal store locked")

    injectors = [ExplodingInjector()] + _make_injectors({})
    loop = _make_loop()

    legacy = _legacy_prerun(loop, injectors, TASK)
    bridged = _hub_prerun(loop, injectors, TASK)

    # 爆炸的 goal 被跳过，但第二个 goal injector（同 order，链继续）
    # 与 memory 照常注入——两边逐字节一致
    assert legacy == bridged == (
        MEMORY_BLOCK + "\n\n" + GOAL_BLOCK + "\n\n" + TASK
    )


def test_injector_contracts_affect_accounting():
    """每条 item 的 tokens 由 hub 统一口径计入账本。"""
    injectors = _make_injectors({})
    loop = _make_loop()

    hub = ContextHub()
    hub.register(build_prerun_context_provider(loop, injectors))
    bundle = hub.build(ContextQuery(task_text=TASK))

    assert bundle.accounting["per_provider"]["prerun_context"]["items"] == 2
    assert bundle.accounting["total_tokens"] == sum(
        i.tokens for i in bundle.items
    )
