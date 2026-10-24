# 架构讨论纪要 — 2026-10

> 用途：**讨论记录 + 决策索引**，后续讨论的起点。
> 性质：不重复实现细节，只记「讨论了什么、达成了什么、还差什么」；细节指向各专项文档。
> 参与：架构师视角 + agent 设计视角 · 借鉴 OpenClaw（392k★）· 与 DSH 对标

---

## 1. 讨论线索（按时间顺序）

| 轮次 | 主题 | 结论一句话 | 详情 |
|---|---|---|---|
| 1 | 六层架构梳理（六遍评审起手） | 分层合理；问题在层内巨文件与层间反向依赖 | `architecture-reeval-20261008.md` |
| 2 | 依赖方向审计 | **3 条 core→api 反向依赖**（event_store→projector / langgraph→bridge / scheduler→web_session） | `architecture-reeval-20261008.md` |
| 3 | SSE 双轨 | 契约应从**事件日志 fold**，不建独立存储；D/C 两处我此前设计有误 | `architecture-reeval2-20261008.md` |
| 4 | Agent 设计（上下文/预算/记忆） | **上下文无仲裁者**（3 套估算、validate() 死契约、记忆不写不读） | `agent-design-reeval-20261008.md` |
| 5 | 目标对齐（自主量化研究 agent） | **自主执行+评估强，缺导演层**（hypothesis 原语有 API 无工具出口） | `goal-alignment-review-20261008.md` |
| 6 | DSH「一切都是插件」 | 借「行为插件化」，**保留**「不变量独立于插件」 | `dsh-plugin-reeval-20261008.md` |
| 7 | 汇总 + RSI | 三层基因组：Tier0 冻结 / Tier1 提案 / Tier2 可自改；**先决条件是 holdout + 不变量层** | `architecture-v2-and-rsi-20261008.md` |
| 8 | 编排层成员边界 | **控制面 3–4 套 → 1 套**；AgentLoop 是「能力」不是「编排」 | `orchestration-layer-membership.md` |
| 9 | 编排层三件套（控制面/策略/环境） | 三者是**一条链 + 一个贯穿环境**；RunContext 是准入产物，Director 产出 = BranchContext | `orchestration-triad-design.md` |
| 10 | **统一入口**（用户提出） | **入口不统一，后面全是空谈**——22 个启动点 / 8 种启动方式 | `unified-entry-design.md` |
| 11 | OpenClaw 对标 | 1 控制面 + N 执行面；执行面可换是它第一原则 | `industry-benchmark-openclaw-20261008.md` |
| 12 | 目标架构 v5 | 管线：接入 → 统一入口 → 控制面 → 策略/环境 → 执行 → 上下文 → 研究域 → 数据 | `architecture-diagram.html`（v5） |

---

## 2. 关键发现（每条附证据）

### 2.1 结构债（Layer & Structure）

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| **①** | **3 条 core→api 反向依赖** | `event_store→projector`（惰性 import）· `langgraph_engine→bridge_v2`（SSE 双轨）· `scheduler→web_session._get_db`（私有符号泄漏） | 分层叙事失效 |
| **②** | **SSE 双轨** | 旧 `SSEEventBuffer` + 原生 `subscribe()` 并存；bridge_v2 是半截迁移的胶水 | 消除双轨 + 消 ① 中两条 |
| **③** | **持久化无统一契约** | 11 个 store/db 模块 | 契约文档 + 一致性测试（0.4） |
| **④** | **上下文无仲裁者** | 3 套 token 估算（`//4` / `3.0` / `token_utils`）· `validate()` 0 调用 | ContextHub 独占装配与预算 |
| **⑤** | **记忆断裂** | `PersistentMemory.add` 调用方只有人工 CLI / MCP；agent 不写；注入只 pre-run 一次且拼进 task 字符串 | 记忆回路断（G2） |
| **⑥** | **导演层缺失** | `HypothesisStore.derive/contradicts` 有 API 无工具出口；`propose` = 0 | 自主性缺一块（D1/D2） |
| **⑦** | **引擎层反向依赖 AgentLoop** | `AgentExecutor`×5 · `executor`×13 | AgentLoop 是能力不是编排 |
| **⑧** | **无 write fence** | `fence` 命中 35 处全在无关文件；取代的 run 可提交陈旧数据 | 并发正确性缺口 |
| **⑨** | **入口 8 种启动方式** | 22 实例化点 / 15 文件；CLI=同步 / API=asyncio.Queue / scheduler=Semaphore / MCP=无 | 「各做各的」的根源 |

### 2.2 结构债（规模与重复）

| 项 | 数值 |
|---|---|
| >800 行文件 | 25 个 / 28k 行 = 21% src（最大：goal/store 1773 · study 路由 1642 · study store 1619 · session service 1450） |
| AgentLoop 拆解轴 | 7 mixin × 9 step **6 组一一重叠**（step 是薄壳） |
| 注册表 | 5 套并存（plugin registry / TOOLSETS / provider `_REGISTRY` / data_store_registry / MCP register） |
| 调度 | 3 处（service / runner / scheduler） |
| 控制面 | 3–4 套（SessionService / StudyRunner / SwarmRuntime / Workflow=工具） |
| `PROTOCOL_VERSION` | **0 处**（协议无版本） |

### 2.3 我们的资产（不可动）

| 资产 | 业界对标 |
|---|---|
| **研究方法论**：假设 → 派生 → 证伪(claims) → AEGIS 新颖性/回归门 → 两阶段 journal | **无直接对标物** |
| **70 题 golden eval + nightly 跟幅 + pyright 回归门禁** | 多数项目没有可比的门禁资产 |
| **类型债治理机制**（分批 + triage + 门禁） | 罕见 |
| **hook 红线**（只读不改控制流） | 与 OpenClaw 一致 ✅ |
| **Tier 0 不变量冻结** | 比 OpenClaw 更硬（他们 sandbox 默认关） |

---

## 3. 已拍板的决策（按时间）

| # | 决策 | 决定 | 理由 / 影响 |
|---|---|---|---|
| 1 | 控制面是否统一为一个 | ✅ **接受** | chat 与 study 共用 SessionController；最大一刀，也是最大简化 |
| 2 | SwarmDAG 归属 | ✅ **编排层内一格**（标注 DAG 驱动） | 决策 2 |
| 3 | Director 归属 | ✅ **编排层内一格**（策略，不是循环） | 决策 3 |
| 4 | AgentLoop 归属 | ✅ **移入能力层**（问题 ⑦ 已决） | 新切 0.11 |
| 5 | 统一入口优先级 | ✅ **E1 插到 A1 之前** | 用户判断：入口不统一后面全是空谈 |
| 6 | P2 研究域强化时机 | ✅ **等控制面 OK 后再动** | 避免同时改 `phase_engine.py` / `runner.py` |
| 7 | P0 是否两人并行 | ⏸ **讨论中** | A1 与 A2–A5 无文件重叠，可并行 |
| 8 | 契约（RoundOutcome/BranchContext）是否独立 | ⏸ **未决** | 倾向：该做（零风险） |
| 9 | `core/study/` 拆 method/ + engine/ | ⏸ **未决** | 倾向：现在做 |
| 10 | RunContext 放 `core/orchestration/` | ⏸ **未决** | 倾向：中立层 |
| 11 | chat 路径无预算门是否立刻补 | ⏸ **未决** | 倾向：立刻补（运行时风险，0.5d） |

---

## 4. 架构演进（v1 → v5）

| 版本 | 核心表述 | 变化 |
|---|---|---|
| **v1** | 六层 + 四中枢 | 分层合理；中枢是「工作集 / 供给 / 事实 / 底线」 |
| **v2** | 三编排嵌套 + mixin↔step 表 | ⑦ 发现（引擎反向依赖 AgentLoop）；goal 归为账本 |
| **v3** | AgentLoop → 能力层 + 3 驱动循环 + Director | 用户三条决策落地 |
| **v4** | 1 控制面 + N 执行面 + 上下文独占 + 不变量层 | OpenClaw 借鉴；总纲 `architecture-v3-openclaw-inspired.md` |
| **v5** | **管线**：接入 → 统一入口 → 控制面 → 策略/环境 → 执行 → 上下文 → 研究域 → 数据 | 用户提出统一入口；E1 前置 |

---

## 5. 落地计划（合并后唯一起手顺序）

### P0 脊柱（每步可独立合入）

| 序 | 刀 | 内容 | 估工 |
|---|---|---|---|
| 1 | **E1 统一入口协议** | `WorkRequest` / `RunHandle` / `WorkEntry` | 1d |
| 2 | **0.1–0.4 结构可信** | scheduler 泄漏 · SSE 双轨 · 去反向 · 持久化契约 | 4d |
| 3 | **A3 write fence** | activeRunId + 提交校验 | 1d |
| 4 | **A1 runtime 协议** | AgentRuntime + 选择器 + 两实现 | 2d |
| 5 | **A2 SessionController** | 7 阶段 + 队列模式 + 6 真值（等价迁移） | 2d |
| 6 | **A4 ContextHub** | collect/arbitrate/render + 1 provider 试点 | 1.5d |
| 7 | **A5 不变量层** | 2 静态 + 1 运行时不变量 | 1d |
| 8 | **E2–E4 入口迁移** | SessionService / scheduler / CLI / MCP 逐个走 submit() | 3.5d |
| 9 | **E5 幂等键** | 副作用方法可安全重试 | 0.5d |
| 10 | **0.5 门禁** + **0.6–0.7 接缝** | CI 门禁 · 统一注册 · 一致性套件 | 2.5d |

### P1 收敛（3–4 周）

`B1` 编排层收敛 · `B2` 缓存边界 + 分层预算 + 归属 · `B3` 协议版本化 + CI 门禁 · `B4` 身份分离 · `B5` 记忆三改 + 写回

### P2 研究域（等控制面 OK）

`C1` 方法论归位 `core/study/method/` · `C2` Director 落地（propose_next）· `C3` 失败降级阶梯 · `C4` 记忆清除边界 · `C5` 单一调度器收敛

### P3 RSI（前置全满足）

`D1` holdout 集 · `D2` Tier 1 提案通道 · `D3` 晋级门禁 · `D4` 观察期与自动回滚

---

## 6. 文档索引

| 文档 | 内容 |
|---|---|
| **`architecture-v3-openclaw-inspired.md`** | ★ 目标架构总纲（1 控制面 + N 执行面 + 上下文独占） |
| `architecture-diagram.html` | v5 管线图（对照讨论用）· 同 `.svg` |
| `unified-entry-design.md` | 统一入口（E1–E5） |
| `orchestration-triad-design.md` | 控制面 / 策略 / 环境 三件套边界 |
| `orchestration-layer-membership.md` | 编排层成员（含 3 条决策） |
| `industry-benchmark-openclaw-20261008.md` | OpenClaw 逐维度对标 |
| `openclaw-architecture.html` | OpenClaw 原生架构图 + 同构对照 |
| `architecture-reeval-20261008.md` · `architecture-reeval2-20261008.md` | 依赖方向 · SSE 双轨 |
| `agent-design-reeval-20261008.md` · `goal-alignment-review-20261008.md` | 上下文无仲裁 · 导演层缺失 |
| `dsh-plugin-reeval-20261008.md` | DSH 插件模式借鉴与保留 |
| `refactor-master-plan-20261008.md` | ★ 唯一起手顺序与护栏（P0–P3 + 具体刀号） |
| `context-hub-discussion.md` · `context-hub-membership-rule.md` | ContextHub 职责与成员边界 |
| `plug-in-contract-design.md` · `agent-g1-g2-design.md` | 接缝规范 · ContextPlan / 记忆闭环 |

---

## 7. 下一步

**如果同意落地顺序**：从 **E1（统一入口协议，1d）** 起步——它定了 `WorkRequest` / `RunHandle` / `WorkEntry` 三个契约，之后 A1–A5、B1–B5 都在这个地基上搭。

**如果还想讨论**：建议先定这两条
1. **E1 的 `queue_mode` 语义**（steer / followup / collect / interrupt 在我们这里各自对应什么）
2. **Director 的产出是否就复用 `BranchContext`**（我倾向是，避免再造第三套契约）
