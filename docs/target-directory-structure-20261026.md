# 目标代码目录结构 — 2026-10-26

> 性质：重构后 `src/strategy_research/` 的目标布局，**后续每一刀的落位检查单**。
> 三条已确认的落位规则（2026-10-26 用户确认）：
> 1. `core/gateway/` 零 FastAPI import（CI import guard 守，0.5）
> 2. `core/orchestration/` 只放跨界契约，不放实现（controller/engine 各有其家）
> 3. 业务域（study/goal/hypothesis/backtest…）目录不动，只做内部重组

---

## 1. 目标结构

```
src/strategy_research/
├─ core/                          # 平台核心（业务无关）
│  ├─ gateway/                    # ★新建 —— Gateway 本体（A2/A3/C5）
│  │  ├─ __init__.py              #   start/stop 生命周期（挂载式单进程）
│  │  ├─ session_controller.py    #   A2：7 阶段准入 / 6 真值
│  │  ├─ capacity.py              #   A2：容量组闸（chat/study/cron）
│  │  ├─ fencing.py               #   A3：activeRunId 认领 + 提交校验
│  │  └─ scheduler.py             #   C5：唯一调度器（收敛现有 3 处）
│  │
│  ├─ orchestration/              # ✅E1 已建 —— 跨界契约与边界
│  │  ├─ contracts.py             #   ✅ WorkRequest / QueueMode / Receipt
│  │  ├─ mailbox.py               #   ✅ 排队域（lane/steer/cap/deadline）
│  │  ├─ entry_point.py           #   ✅ 协议端点门面
│  │  ├─ config_hub.py            #   ✅ 配置中心
│  │  ├─ budget_gate.py           #   ✅ chat 预算门
│  │  ├─ branch_contracts.py      #   ✅ RoundOutcome/BranchContext 中立协议
│  │  ├─ runtime_hub.py           #   A1：preset 图 + provider/模型解析
│  │  └─ run_context.py           #   A4：预算/事件/取消/追踪 per-run 包
│  │
│  ├─ engine/                     # ★新建 —— 执行面（A1/B1）
│  │  ├─ graph_engine.py          #   GraphEngine（包裹 langgraph，支持环）
│  │  ├─ nodes/                   #   llm / react / cpu / graph
│  │  ├─ presets/                 #   chat / research(T0 冻结) / fanout(T1)
│  │  ├─ executor_bridge.py       #   dispatch/events/cancel/heartbeat
│  │  └─ executor/                #   常驻子进程池 + Job Object + T3 沙箱位
│  │
│  ├─ context/                    # A4 —— ContextHub（独立目录，被所有节点调用）
│  │  ├─ hub.py                   #   collect→arbitrate→render→explain
│  │  └─ providers/               #   goal/todo/memory/hypothesis/knowledge
│  │
│  ├─ events/                     # ✅已有（event_v2.py 保留）
│  ├─ storage/                    # 0.4 —— 数据面（合并进现有目录，不新建平行目录）
│  │  ├─ platform_store.py        #   唯一写入者（不变量 7）
│  │  ├─ session_db.py            #   每 session 自包含库
│  │  ├─ registry.py              #   P-3 工件登记 + goal 摘要索引
│  │  └─ migration.py             #   键控迁移表
│  │
│  ├─ agent/                      # A1 迁移后瘦身（loop.py → engine/nodes/react）
│  ├─ study/                      # C1 拆分 → method/ + engine/（业务保留）
│  ├─ goal/ hypothesis/ alpha_zoo/ backtest_engine/ ...  # 业务域不动
│  └─ registry.py                 # ✅已有 → 扩展为 CapabilityRegistry
│
├─ api/                           # E2 后：路由只做 adapter（构造 WorkRequest）
├─ cli/                           # E3：CLI 作为客户端（HTTP 或进程内 adapter）
├─ mcp/                           # E4：MCP 作为 adapter
└─ ...
tests/
├─ test_entry_contracts*.py · test_config_hub.py · test_budget_gate.py
├─ test_branch_contracts.py · test_orchestration_finishing_extended.py    ✅E1
└─ test_gateway_*.py · test_engine_*.py · test_storage_*.py              后续刀
docs/                              # 架构与决策文档（不随代码结构变动）
```

## 2. 已确认的三个布局决定

| # | 决定 | 理由 |
|---|---|---|
| 1 | **engine 与 orchestration 平级** | engine 是执行面（重），orchestration 是边界契约（轻）；混放会让契约层依赖执行层 |
| 2 | **context/ 独立目录** | ContextHub 被所有节点调用，且是「模型看到什么」的唯一所有者 |
| 3 | **storage/ 合并进现有 core/storage/** | 现有 event_schema.py 已在此，往内加 platform_store/session_db/registry，不新建平行目录 |

## 3. 现有文件迁移映射（替换评估的落位版）

| 现有文件 | 去向 | 刀号 | 处置 |
|---|---|---|---|
| `core/agent/loop.py`（AgentLoop + 7 mixin + 9 step） | `core/engine/nodes/react/` | A1 | 平移为 react 节点内部；7 mixin 部分上交引擎服务 |
| `core/agent/loop_stream.py` | `core/engine/nodes/react/streaming.py` | A1 | 随 loop 平移 |
| `core/agent/loop_compaction.py` | `core/context/` + 节点内 | A1/A4 | diagnose 归 ContextHub、treat 归节点 |
| `core/agent/event_store.py` | `core/storage/platform_store.py` + `core/events/` | 0.4 | 接口保留，存储形态改分库 |
| `core/study/phase_engine.py` | `core/engine/presets/research/` + `core/study/method/` | B1/C1 | 阶段图 → preset 数据；方法论 → method/ |
| `core/swarm/runtime.py` | 删除（控制面部分）+ fanout 解释器 | B1 | **控制代码死亡不搬家** |
| `api/session/service.py` 的 `_session_queues` | `core/orchestration/mailbox.py` | A2/E2 | 队列归一，`_process_session_queue` 拆解 |
| `core/agent/loop.py` 的 `_inbox`（steering） | `mailbox.py` steer 分支 | A4 | 现有 30+ 测试为验收基线 |
| `core/seq_generator.py` | event_log 权威 seq | 0.4 | 兼容层或删除 |
| `core/agent/strategy/budget.py` | 保留，A1 与 budget_gate 分层接线 | A1 | **不删**（有 cost_usd 维度） |
| `core/session/rate_limiter.py` | 保留 | — | 写入限流，与 LLM 限流不同层 |
| `api/sse_buffer.py` + `api/session/bridge_v2.py` | 删除（归一 SSE 单轨） | 0.2 | 先立新后删旧 |
| `api/routers/*` | 改 adapter（构造 WorkRequest） | E2 | 逐 router 切换 |
| `cli/*` | 客户端化 | E3 | 无 daemon 时明确报错 |
| `core/study/scheduler.py` 等 3 处调度 | `core/gateway/scheduler.py` | C5 | 唯一调度器 |

## 4. 落位规则（每刀自检）

1. 新文件必须落在上表位置；跨目录 import 必须符合方向（gateway 零 FastAPI；orchestration 零 engine/agent import）
2. 业务域目录（goals/hypothesis/study/backtest…）**只做内部重组，不搬位置**
3. 删除现有文件必须同时删除对应测试（或迁移测试），不留孤儿测试
4. 每刀合入前跑全量 E1 契约测试（88 条）确保边界不破
