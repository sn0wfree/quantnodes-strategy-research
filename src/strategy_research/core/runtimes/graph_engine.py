"""engine —— 执行面核心（阶段 B1-a：阶段图）。

GraphEngine：把「一张图」跑起来，节点间传递 state dict。
langgraph StateGraph 承载（原生支持环）；事件经注入 sink 发出。

图语义（B1-a，详见 docs/stage-b1a-graph-engine-design-20261027.md）：
- add_node 只注册；默认出边 = 指向下一个注册节点（末节点 → END），
  因此 1a 的「线性串联」用法行为不变。
- add_edge / add_conditional_edge 声明即覆盖该节点的默认边；
  每个节点一条出边（固定或条件）。
- 中断：节点抛 GraphInterrupt → state 写入 ``__interrupt__`` 并在
  节点边界停图（全边守卫，固定边同样受守卫）；已完成节点的结果保留。
- max_hops：跃点计数超限以协作中断停止（langgraph recursion_limit
  仅作硬兜底）。

约定：
- 节点签名 `fn(state: dict) -> dict`，返回**完整更新后的 state**（自行合并）。
- ``__interrupt__`` 是引擎保留键，节点 fn 不得写入。
- 引擎不认识 LLM/工具——节点自带依赖（llm 节点持有 client）。
- 引擎不落库——事件走 sink，持久化属数据面（PlatformStore）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from langgraph.graph import END as _LG_END
from langgraph.graph import StateGraph

EventSink = Callable[[str, dict], None]
NodeFn = Callable[[dict], dict]
RouterFn = Callable[[dict], str]

# langgraph 的 END 是 "__end__" 字符串常量；re-export 给 router/mapping 用。
END: str = _LG_END
INTERRUPT_KEY = "__interrupt__"
_MAX_HOPS_BACKSTOP = 5


class GraphInterrupt(Exception):
    """协作式中断：在节点边界停图，state 保留 ``__interrupt__`` 标记。

    与普通异常的区别：普通异常直接冒出 run()（引擎崩溃，state 丢弃）；
    GraphInterrupt 是「有理由的提前结束」，调用方拿到部分结果 + 原因。
    """

    def __init__(self, reason: str, data: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.data = data


class GraphEngine:
    """阶段图执行器：默认直线，声明边则覆盖；支持环 / 条件路由 / 协作中断。"""

    def __init__(self, *, sink: EventSink | None = None, max_hops: int = 50) -> None:
        self._sink = sink or (lambda kind, data: None)
        self._max_hops = max_hops
        # 注册顺序决定默认直线；_order 保序，_fns 查函数。
        self._order: list[str] = []
        self._fns: dict[str, NodeFn] = {}
        # 出边覆盖表：节点名 -> ("edge", target) | ("cond", router, mapping|None)
        self._out: dict[str, tuple] = {}
        self._entry: str | None = None

    # ── 构图 ─────────────────────────────────────────────

    def add_node(self, name: str, fn: NodeFn) -> "GraphEngine":
        if name in self._fns:
            raise ValueError(f"节点 {name!r} 已注册")
        if name == END:
            raise ValueError("节点名不能是保留的 END")
        self._order.append(name)
        self._fns[name] = fn
        return self

    def set_entry(self, name: str) -> "GraphEngine":
        self._require_node(name, "set_entry")
        self._entry = name
        return self

    def add_edge(self, source: str, target: str) -> "GraphEngine":
        self._require_node(source, "add_edge source")
        if target != END:
            self._require_node(target, "add_edge target")
        self._out[source] = ("edge", target)
        return self

    def add_conditional_edge(
        self,
        source: str,
        router: RouterFn,
        mapping: dict[str, str] | None = None,
    ) -> "GraphEngine":
        """条件边：router(state) -> key；mapping[key] -> 节点名或 END。

        mapping 省略时 router 直接返回节点名或 END。
        router 返回未知 key 在 run 时 fail-loud。
        """
        self._require_node(source, "add_conditional_edge source")
        if mapping is not None:
            for key, target in mapping.items():
                if target != END:
                    self._require_node(target, f"add_conditional_edge mapping[{key!r}]")
        self._out[source] = ("cond", router, mapping)
        return self

    # ── 执行 ─────────────────────────────────────────────

    def run(self, state: dict, *, start: str | None = None) -> dict:
        """执行图；返回最终 state（可能带 ``__interrupt__`` 标记）。

        start：本次运行的入口节点（恢复场景），不影响后续默认边。
        """
        if not self._order:
            raise ValueError("GraphEngine 至少需要一个节点")
        if start is not None:
            self._require_node(start, "run start")
        if INTERRUPT_KEY in state:
            # 已中断的 state 不可执行：守卫在出边上，拦不住首跳，
            # 这里直接短路。恢复 = 调用方 pop 标记（可能配合 start=）。
            return dict(state)

        hops = {"n": 0}
        graph = StateGraph(dict)
        for name in self._order:
            graph.add_node(name, self._wrap(name, self._fns[name], hops))
        graph.set_entry_point(start or self._entry or self._order[0])
        for name in self._order:
            graph.add_conditional_edges(name, self._router_for(name), None)
        app = graph.compile()

        result = app.invoke(
            dict(state),
            config={"recursion_limit": self._max_hops + _MAX_HOPS_BACKSTOP},
        )
        return dict(result)

    # ── 内部 ─────────────────────────────────────────────

    def _require_node(self, name: str, where: str) -> None:
        if name not in self._fns:
            raise ValueError(f"{where}: 节点 {name!r} 未注册（先 add_node 再声明边）")

    def _router_for(self, source: str) -> RouterFn:
        """统一出边路由：全边守卫（中断即 END）+ 默认直线 + 声明覆盖。"""
        wiring = self._out.get(source)

        def route(state: dict) -> str:
            if INTERRUPT_KEY in state:
                return END
            if wiring is None:
                idx = self._order.index(source)
                return self._order[idx + 1] if idx + 1 < len(self._order) else END
            if wiring[0] == "edge":
                return wiring[1]  # type: ignore[return-value]
            _, router, mapping = wiring  # type: ignore[misc]
            key = router(state)
            target = mapping.get(key) if mapping is not None else key
            if target is None:
                raise ValueError(
                    f"节点 {source!r} 的 router 返回未知 key {key!r}（不在 mapping 中）"
                )
            if target != END and target not in self._fns:
                raise ValueError(
                    f"节点 {source!r} 的 router 路由到未注册节点 {target!r}"
                )
            return target  # type: ignore[return-value]

        return route

    def _wrap(self, name: str, fn: NodeFn, hops: dict) -> NodeFn:
        def wrapped(state: dict) -> dict:
            hops["n"] += 1
            if hops["n"] > self._max_hops:
                data = {"node": name, "reason": "max_hops", "hops": hops["n"]}
                self._sink("graph.interrupted", data)
                return {**state, INTERRUPT_KEY: data}
            started = time.perf_counter()
            self._sink("node.started", {"node": name})
            try:
                updated = fn(dict(state))
            except GraphInterrupt as gi:
                data: dict[str, Any] = {"node": name, "reason": gi.reason}
                if gi.data is not None:
                    data["data"] = gi.data
                self._sink("graph.interrupted", data)
                return {**state, INTERRUPT_KEY: data}
            self._sink(
                "node.completed",
                {"node": name, "seconds": round(time.perf_counter() - started, 4)},
            )
            return updated

        return wrapped
