"""engine —— 执行面（阶段 1a 起步）。

GraphEngine：把「一张图 = 有序节点序列」跑起来，节点间传递 state dict。
1a 用 langgraph StateGraph 承载（支持环，1b 的 react 节点需要）；
事件经注入 sink 发出（node.started / node.completed），带耗时。

约定：
- 节点签名 `fn(state: dict) -> dict`，返回**完整更新后的 state**（自行合并）。
- 引擎不认识 LLM/工具——节点自带依赖（llm 节点持有 client）。
- 引擎不落库——事件走 sink，持久化属 0.4 数据面。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from langgraph.graph import StateGraph, END

EventSink = Callable[[str, dict], None]
NodeFn = Callable[[dict], dict]


class GraphEngine:
    """线性图执行器（langgraph 承载）。1b 在此加环与条件边。"""

    def __init__(self, *, sink: EventSink | None = None) -> None:
        self._sink = sink or (lambda kind, data: None)
        self._nodes: list[tuple[str, NodeFn]] = []

    def add_node(self, name: str, fn: NodeFn) -> "GraphEngine":
        self._nodes.append((name, fn))
        return self

    def run(self, state: dict) -> dict:
        """按序执行节点；每节点前后发事件；返回最终 state。"""
        if not self._nodes:
            raise ValueError("GraphEngine 至少需要一个节点")
        graph = StateGraph(dict)
        for name, fn in self._nodes:
            graph.add_node(name, self._wrap(name, fn))
        graph.set_entry_point(self._nodes[0][0])
        for (_, name_a), (name_b, _) in zip(self._nodes, self._nodes[1:]):
            graph.add_edge(name_a, name_b)
        if len(self._nodes) > 1 or True:
            graph.add_edge(self._nodes[-1][0], END)
        app = graph.compile()

        result = app.invoke(dict(state), config={"recursion_limit": 50})
        return dict(result)

    def _wrap(self, name: str, fn: NodeFn) -> NodeFn:
        def wrapped(state: dict) -> dict:
            started = time.perf_counter()
            self._sink("node.started", {"node": name})
            updated = fn(dict(state))
            self._sink(
                "node.completed",
                {"node": name, "seconds": round(time.perf_counter() - started, 4)},
            )
            return updated

        return wrapped
