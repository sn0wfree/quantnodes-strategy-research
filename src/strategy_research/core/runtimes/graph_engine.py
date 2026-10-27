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

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import END as _LG_END
from langgraph.graph import StateGraph

from .run_budget import RunBudget

EventSink = Callable[[str, dict], None]
NodeFn = Callable[[dict], dict]
RouterFn = Callable[[dict], str]
WorkerFn = Callable[[Any, dict], dict]

# langgraph 的 END 是 "__end__" 字符串常量；re-export 给 router/mapping 用。
END: str = _LG_END
INTERRUPT_KEY = "__interrupt__"
_MAX_HOPS_BACKSTOP = 5


@dataclass(frozen=True)
class _FanoutSpec:
    """fanout 复合节点声明（挂在拆分节点上）。详见 stage-f1-fanout-design。"""

    worker: WorkerFn
    join: str
    mode: str
    max_parallel: int


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
        # fanout 复合节点声明表：拆分节点名 -> spec
        self._fanouts: dict[str, _FanoutSpec] = {}
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

    def add_fanout(
        self,
        source: str,
        worker: WorkerFn,
        join: str,
        *,
        mode: str = "t1",
        max_parallel: int = 8,
    ) -> "GraphEngine":
        """fanout 复合节点（多 subagent 并行，F1）。

        拆分节点 fn 返回 {"branches": [载荷, ...]}；每条分支在
        ``{**父state快照, "branch": {id, ordinal, payload}}`` 上跑 worker，
        返回值不并入父 state（账本隔离）。join 节点从
        ``state["fanout_results"]`` 读全貌并负责合并。

        失败两档：分支普通异常 → 记 error、其余照跑、汇合照常；
        分支抛 GraphInterrupt → 连坐（未起跑分支记 cancelled）、汇合不执行、
        state 带 ``__interrupt__``。T1 分支跑在线程里，取消协作粒度 =
        整个 worker（Python 无法硬杀线程），如实见 stage-f1-fanout-design §3。
        """
        self._require_node(source, "add_fanout source")
        self._require_node(join, "add_fanout join")
        if join == source:
            raise ValueError("add_fanout: join 不能与 source 是同一节点")
        if source in self._fanouts:
            raise ValueError(f"add_fanout: 节点 {source!r} 已声明 fanout")
        if mode != "t1":
            raise ValueError("add_fanout: mode='t2' 属 F4（executor 池接线），尚未支持")
        if max_parallel < 1:
            raise ValueError("add_fanout: max_parallel 至少为 1")
        if not callable(worker):
            raise TypeError("add_fanout: worker 必须可调用")
        self._fanouts[source] = _FanoutSpec(
            worker=worker, join=join, mode=mode, max_parallel=max_parallel,
        )
        # 声明即覆盖：拆分节点的出边强制指向汇合节点。
        self._out[source] = ("edge", join)
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
        spec = self._fanouts.get(name)

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
                if spec is not None and INTERRUPT_KEY not in updated:
                    updated = self._run_fanout(name, spec, updated)
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

    def _run_fanout(self, node_name: str, spec: _FanoutSpec, state: dict) -> dict:
        """并行执行分支（父线程统一发事件，sink 不跨线程）。

        业务失败不连坐（error 条目进 fanout_results，汇合照常）；
        GraphInterrupt 连坐（取消旗拦未起跑分支，state 带中断标记，
        路由守卫把图送到 END——join 不执行）。
        """
        branches = state.get("branches")
        if not isinstance(branches, list):
            raise ValueError(
                f"fanout 拆分节点 {node_name!r} 的 fn 必须返回 {{'branches': list}}"
            )
        parent_run = state.get("run_id")
        self._sink("graph.fanout", {
            "node": node_name,
            "branches": len(branches),
            "max_parallel": spec.max_parallel,
        })
        cancel = threading.Event()
        # 预算树（F2，规则④）：state["run_budget"] 是父 RunBudget → 拆分时
        # 均分预留，每支分支拿自己的切片（branch.budget），落定后 settle 归还。
        # 无约定键则 branch.budget=None，零打扰。
        parent_budget = state.get("run_budget")
        slices: list[RunBudget] | None = None
        if isinstance(parent_budget, RunBudget) and branches:
            slices = parent_budget.reserve(len(branches))

        def run_one(ordinal: int, payload: Any) -> dict:
            branch_id = f"{parent_run}:{ordinal}" if parent_run else f"{node_name}:b{ordinal}"
            entry: dict[str, Any] = {
                "ordinal": ordinal,
                "branch_id": branch_id,
                "payload": payload,
                "result": None,
                "error": None,
                "seconds": 0.0,
            }
            if cancel.is_set():
                entry["status"] = "cancelled"
                return entry
            branch_started = time.perf_counter()
            branch_state = {
                **state,
                "branch": {
                    "id": branch_id,
                    "ordinal": ordinal,
                    "payload": payload,
                    "budget": slices[ordinal - 1] if slices else None,
                },
            }
            try:
                result = spec.worker(payload, branch_state)
            except GraphInterrupt as gi:
                cancel.set()
                entry["status"] = "interrupted"
                entry["error"] = gi.reason
                entry["seconds"] = round(time.perf_counter() - branch_started, 4)
                return entry
            except Exception as exc:  # noqa: BLE001 —— 业务失败不连坐
                entry["status"] = "error"
                entry["error"] = str(exc)
                entry["seconds"] = round(time.perf_counter() - branch_started, 4)
                return entry
            entry["seconds"] = round(time.perf_counter() - branch_started, 4)
            if isinstance(result, dict) and INTERRUPT_KEY in result:
                # 防御：worker 用返回值夹带中断标记，按中断处理
                cancel.set()
                entry["status"] = "interrupted"
                entry["error"] = str(result.get(INTERRUPT_KEY))
                return entry
            entry["status"] = "ok"
            entry["result"] = result
            return entry

        with ThreadPoolExecutor(max_workers=spec.max_parallel) as pool:
            futures = [pool.submit(run_one, i + 1, p) for i, p in enumerate(branches)]
            by_ordinal: dict[int, dict] = {}
            for fut in as_completed(futures):
                entry = fut.result()
                by_ordinal[entry["ordinal"]] = entry
                self._sink("branch.settled", {
                    "node": node_name,
                    "branch_id": entry["branch_id"],
                    "ordinal": entry["ordinal"],
                    "status": entry["status"],
                    "seconds": entry["seconds"],
                })

        results = [by_ordinal[i + 1] for i in range(len(branches))]
        if slices:
            parent_budget.absorb(*slices)  # settle 归还（父线程，单点）
        out = {**state, "fanout_results": results}
        interrupted = next((e for e in results if e["status"] == "interrupted"), None)
        if interrupted is not None:
            data: dict[str, Any] = {
                "node": node_name,
                "reason": interrupted["error"] or "branch_interrupt",
                "data": {"branch_id": interrupted["branch_id"]},
            }
            self._sink("graph.interrupted", data)
            out[INTERRUPT_KEY] = data
        return out
