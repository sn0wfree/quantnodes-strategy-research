# 项目现状梳理与路线图 — 2026-10-28

> 实测数据（本机 Python 3.12 环境），非估算。用于对齐「现在在哪、下一步做什么」。

---

## 1. 代码规模（实测）

| 区域 | 文件 | 行数 |
|---|---|---|
| **src 总计** | 969 | **116,622** |
| core/agent | 90 | 18,195 |
| api | 46 | 13,272 |
| core/study | 44 | 12,371 |
| cli | 70 | 10,906 |
| core/workflow | 15 | 2,309 |
| core/storage | 9 | 1,684 |
| core/runtimes | 9 | 1,172 |
| core/mcp | 2 | 782 |
| core/orchestration | 7 | 696 |
| core/session | 6 | 605 |
| core/gateway | 7 | 513 |
| core/context | 3 | 155 |
| **tests** | 638 | **119,323** |
| **docs** | 253 md + 3 html | — |

单文件最大：`agent/loop.py` 1161、`agent/tools.py` 785、`agent/memory_manager.py` 723、`agent/compact.py` 669、`agent/event_store.py` 568。

**读数**：测试与源码接近 1:1（119k vs 117k）。`core/agent` 一个包占源码 15.6%，是最大的单块——它就是 C1 要处理的对象。

---

## 2. 架构现状（v6.2 五层）

| 层 | 状态 | 证据 |
|---|---|---|
| 渠道 | ✅ 已统一 | mailbox 唯一准入（3a/3c） |
| Gateway 控制面 | ✅ 已建成 | 单例 + SessionController + 不变量门禁 |
| 执行面 Runtime | 🟡 **新旧并存** | 新 GraphEngine 1172 行（含 fanout）+ 旧 AgentLoop 18k 行 |
| 平台数据面 | 🟡 部分 | EventStore 门面化已完成（S1）；messages 仍多写者 |
| 研究域 | ✅ 单引擎 | langgraph 分支已退役（P5-2，-2782 行） |

**关键结论**：入口/控制面/研究域三层已收口；**执行面与数据面是剩余的两块硬骨头**，且互为依赖（执行体迁移必须先回答持久化归谁）。

### 2.1 执行面：新旧并存的具体形态

新引擎侧（已完成）：
- `GraphEngine`：阶段图 + 条件边 + 协作中断 + fanout 复合节点（T1/T2）
- `react` 节点：流式 + 工具链 + 预算注入
- `research` preset：study 轮次唯一执行体

旧执行体侧（待迁移）：
- `AgentLoop` 1161 行 + 4 个 mixin（aegis/knowledge/…）+ strategy 步管道
- 3 个真实构造点：`chat_loop.py:129`、`executor.py:347`、`role_factory.py:144`
- 4 条消费链：**chat**（gateway/CLI）、**study**（role_factory）、**workflow**（node_types 5 处）、**swarm**

### 2.2 数据面：messages 写者实况（本轮门禁已钉死）

| 写者 | 目标 | 定性 |
|---|---|---|
| `projection_store.py` | 统一库 | ✅ 设计唯一写者 |
| `web_session.py:persist_message` | 统一库 | ⚠ 兼容直写，已加幂等防护 |
| `memory_manager.py` | 自己的 SQLiteStore | 遗留，C1 处理 |
| `core/session/db.py` | 独立旧库（schema 不兼容） | 遗留，C1 退役 |

**本轮新发现的硬约束**：两套 messages schema 不可合并（`id INTEGER` vs `id TEXT`、`timestamp` vs `created_at`）。已落为门禁测试，防止后人误合。

---

## 3. 遗留面清单（实测）

| 项 | 规模 | 归属 |
|---|---|---|
| 旧 chat 分支 + `new_entry_enabled` 门 | chat.py:875、tui/session.py:236 + 2 导出点 | **P5-1c（已挂起，业务稳定后）** |
| SSE 双轨（`sse_buffer` + `bridge_v2`） | 8 生产 + 7 测试文件 | P5-3（独立工程） |
| AgentLoop 18k 行 | 3 构造点 / 4 消费链 | C1（最大项） |
| MemoryManager 直写 messages | 723 行文件内 2 处 | C1 顺带 |
| `core/session/db.py` 旧会话栈 | 605 行包内 | C1 后退役 |
| `_archived/` + `tests/attic/` | 13 + 29 文件 | 保留（零运行时成本） |
| `to_llm_message` 等历史别名 | 少量 | 随消费方迁移清 |

---

## 4. 测试健康度（实测）

- **可收集测试**：12,900（PR 道 9,862，另有 3,038 为 llm/e2e/slow 道）
- **已知预存阻塞**（非本次改动引入，stash 验证过）：
  - `test_e2e_full_flow`：approval 等待 1800s 后超时（9/5 记录在案的陈旧测试）
  - `test_sse_e2e` / `test_e2e_chat` / `test_e2e_comprehensive` / `test_webui_e2e*`：真实 uvicorn + socket 长阻塞
  - `test_alpha_zoo_adapter` ×2：`compute_as_series` 未按约定 dropna
- **已修复的隔离问题**（本轮）：`test_lifespan_recover` 陈旧 `_scheduler_cache` 引用（2 ERROR → 2 PASS）

**判断**：预存阻塞集中在 live/e2e 测试，单元与集成道基本健康。全量绿的前提是先把这些 live 套件分道 + 加超时。

---

## 5. 本轮（2026-10-27/28）已交付

| 批次 | 内容 | 提交 |
|---|---|---|
| B1-a/b | GraphEngine 阶段图 + research preset | 早期 |
| F1-F4 | fanout 多 subagent（T1/T2 + 预算树） | 早期 |
| 3c + 阶段 4 | scheduler 迁移 + 旧队列/门删除 + 测试隔离 | 早期 |
| P5-1 / P5-1b | Gateway chat 历史/持久化/流式/工具 + MiniMax think 修复 | `af4cb235` `0d25b702` `3d03ed65` |
| 搜索 | LIKE 替代 FTS trigram | `d73054d1` |
| HITL 门 | 审批门 + resume（方案 A）+ 评审修复 | `71d5cf34` `8623d42d` `f639ee2d` |
| P5-2 | langgraph 分支退役（-2782 行） | `5c4c544` |
| P5-4 | ContextHub W0 + pre-run 迁入 + 多 agent 评审修复 | `7e6e183d` `2f17a1da` |
| **P5-5** | **安全债 ×4 清零** | `174ecea1` `4d6696da` `0c02c346` `87802d5e` |
| **0.4 S3** | messages 白名单门禁 + 幂等防护 | `ed883eae` |

---

## 6. 下一步路线（按依赖排序）

### 立即可做（无需前置）

| # | 事项 | 工期 | 说明 |
|---|---|---|---|
| A1 | **测试分道 + 预存阻塞清理** | 2-3d | 把 live/e2e 套件显式分道、加超时；修 `test_e2e_full_flow` 的 approval_timeout；`alpha_zoo` 两条。**这是所有后续验收的基础**——没有稳定基线，后续改动无法判定回归 |
| A2 | P5-3 SSE 单轨 | ~2d | 独立工程，8 生产 + 7 测试文件 |
| A3 | P5-1c 旧 chat 分支 | 0.5-1d | 已挂起，业务稳定后 |

### 依赖链（执行面 + 数据面）

| # | 事项 | 工期 | 依赖 |
|---|---|---|---|
| B1 | **C1 阶段一：chat 链迁 react 节点** | 2-3d | A1（验收基线） |
| B2 | C1 阶段二：study 链迁（role_factory → react） | 2-3d | B1（模式验证） |
| B3 | C1 阶段三：workflow + swarm 链 | 2-3d | B2 |
| B4 | MemoryManager 事件化 + 旧会话栈退役 | 1-2d | B1-B3 |
| B5 | ContextHub W1/W2（todos provider、ContextPlan 仲裁） | 2-3d | B1（react 是消费方） |

**C1 总工期 6-10d**，是剩余工作中最大的一块，也是「GraphEngine 唯一 runtime」这条架构目标能否达成的关键。

### 收尾

| # | 事项 | 工期 |
|---|---|---|
| C1 | 审计门② 五项（清单 §6） | 1d |
| C2 | 文档对齐（253 份 md 中去重/归档过期项） | 1-2d |

---

## 7. 建议顺序

```
A1（测试基线，2-3d）→ A2（SSE 单轨，2d）
   ↓
B1 → B2 → B3（C1 三阶段，6-9d，每阶段独立验收）
   ↓
B4（数据面收尾）→ B5（ContextHub 尾项）
   ↓
A3（P5-1c，可随时插入）→ C1/C2（审计门 + 文档）
```

**为什么 A1 必须最先**：当前 12,900 条测试里有 ~6 个 live 套件会无限阻塞，任何全量验证都跑不完。没有可复现的绿/红基线，后续 6-9 天的 C1 迁移就是在盲飞。

**为什么 C1 分三阶段而非一次做完**：三条链的复杂度差异大（chat 最简、study 有 HITL/预算、workflow/swarm 有并发语义）。每阶段独立验收 + 可回退，避免一次性 18k 行迁移的风险。

**A3（P5-1c）可以随时插入**：它不阻塞任何东西，纯粹是「入口只剩一条路」的整洁性收益。
