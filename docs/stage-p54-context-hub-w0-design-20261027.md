# P5-4 ContextHub W0 骨架 + pre-run 通路迁入 — 2026-10-27

> 调查修正了清单语义：**ContextHub 在代码里不存在**（差距图 §2.5「能力散落
> 3 处」），「4 builder」= `context_injector.py` 的 4 个 injector 实现
> （Goal/MemoryRetriever/Todos/GoalContinuation）。本 stage = 设计文档
> （`context-hub-discussion.md`）的 **W0 骨架落地 + pre-run 通路迁入**，
> 不是纯搬家。

## 1. 范围（对齐 W0/W1 切分）

| 件 | 处置 |
|---|---|
| `core/context/`（protocol + hub 骨架） | **新建**——ContextItem/ContextProvider/ContextQuery/ContextBundle/ContextHub |
| goal + memory pre-run 注入 | **迁入**——桥接为 provider，AgentLoop 组装走 hub |
| TodosInjector（per-iteration + 去重状态） | 留 W1（per-iteration 形态，非 pre-run） |
| GoalContinuationInjector（返回 bool = 决策） | **不转**（设计文档 §4 明文：控制决策不是上下文） |
| ContextBuilder（渲染器角色） | 不动——渲染归渲染器（讨论稿开放问题 1）；G6 另议 |
| ContextPlan 仲裁/裁剪 | 留 W2 |

## 2. 关键发现与形态决策

**检索 query 的链式语义**：旧 pre-run 循环是逐个前缀叠加——goal 注入后的串
作为 memory 检索的 query（`MemoryRetrieverInjector._build_block(loop, task)`
收到的 task 含 goal 块）。逐字节等价要求复现这个叠加。因此：

- **不做逐 injector 拆散桥接**（那会改变检索 query → 行为漂移）；
- **`PreRunContextProvider` 桥接整条 pre-run 链**：provide() 内按 order
  复刻叠加循环，产出按最终渲染序的多 items（memory、goal 各一条，kind
  分开可溯源），内容与旧路径逐字节相同。等价性由构造保证（与 B1-b 同
  方法论），等价测试钉死。
- W1 把链内成员独立成 provider 时，用 ContextQuery.prior_items 解决演进
  语义（设计稿本就预留 prior_kinds）。

## 3. 硬约束（讨论稿 §3，落地为架构门禁）

中枢只依赖协议：`core/context/` **禁止 import** `core/agent`/`core/goal`/
`core/study`——加进 `test_architecture_gates.py`。生产方适配器在
`core/agent/context_providers.py`（域 → 平台方向合法）。

## 4. 接口（与设计稿 §6 的差异如实记录）

```python
hub.register(provider)                     # 注册（生产方侧）
hub.build(query) -> ContextBundle          # 收集（best-effort：provider
                                           # 异常降级为无贡献 + dropped 记录）
bundle.explain() -> dict                   # 保留/丢弃/账本
```

差异：设计稿 `build(query, *, history, tools)` 的 history/tools 形参
**暂不收**——渲染还在 ContextBuilder，history/tools 入 bundle 是 W2
ContextPlan 并入时的事。tokens 用 `token_utils.estimate_tokens` 统一口径
（消三套估算中的 hub 侧一套）。

## 5. 迁移点（AgentLoop）

- `__init__`：构造 `ContextHub` + 注册 `PreRunContextProvider`（替代
  `self._injectors` 的 pre-run 成员；todos/continuation 仍走
  `self._injectors` 的 per-iteration/post-response 通路，不动）。
- `_prepare_run`：`for injector order<0` 循环 →
  `hub.build(ContextQuery(task_text=full_task, session_id=...))` +
  渲染（items 序拼接 + full_task）。
- **行为不变量**：full_task 逐字节等价（等价测试直证）；provider 开关
  （enable_goal_injection / enable_memory_injection）语义不变。
- **契约收窄（如实记录）**：pre-run injector 从「任意 task 变换」收窄为
  **前缀注入**（`block + "\n\n" + task`）——hub 的 items+task 渲染契约
  表达不了任意变换；生态里仅有的两个真实实现（goal/memory）本就是
  前缀式。`test_dsh_deep_integration` 的追加式假件已改为前缀式。

## 6. 测试

- `tests/test_context_hub.py`：best-effort 降级（provider 抛异常不阻断 +
  dropped 记录）、token 账本统一口径、explain、同 query 同 bundle 确定性。
- `tests/test_context_providers_bridge.py`：整链桥接等价（旧 injector
  循环 vs hub 渲染，full_task 逐字节相等，含检索 query 链式语义）；
  开关关闭 → 无贡献；空 task → 无贡献。
- 架构门禁：core/context 零生产方 import。
- 回归：chat_loop / p2_e2e / dsh_deep_integration / role_factory /
  tool_brief / context_builder 全绿。

## 7. 后续（W1/W2 尾项，不在本轮）

W1：todos provider 化（per-iteration）、记忆注入三改（ContextBuilder 的
frozen snapshot + user-message recall 与 MemoryRetriever 合流）、链内
成员独立 provider（prior_items）。W2：ContextPlan 并入仲裁面 + 裁剪 +
snapshot/eval-diff。
