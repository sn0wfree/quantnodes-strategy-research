# Framework Philosophy

> 量化研究系统的长程任务执行框架哲学。

## 1. 核心概念

### Session（会话）

Session 是系统的执行边界。所有任务——用户交互、目标追踪、自动研究——都在 session 内发生。一个 session 对应一个独立的研究方向或工作流。

### Goal（目标）

Goal 是通用的长程化任务抽象。它追踪：
- **objective**：研究目标（自然语言）
- **criteria**：验收标准（量化指标或证据条件）
- **evidence**：支撑每个标准的证据
- **status**：生命周期状态（12 个值）
- **audit**：完成审计

Goal 是**纯记账层**——它记录"要做什么"和"做到什么程度"，但不驱动执行。

### Study（研究）

Study 是量化策略专用的长程化任务。**Study 是 Goal 的特化子类**——两者属性完全一致，但 Study 在 Goal 基础上附加了执行引擎：

| 属性 | Goal（通用） | Study（量化特化） |
|------|------------|-----------------|
| objective / criteria / evidence | ✓ | ✓ |
| status 生命周期 | 12 值（GoalStatus） | 9 值（StudyStatus） |
| 执行引擎 | 无（手动或外部驱动） | AutoresearchExecutor |
| 调度器 | 无 | StudyScheduler |
| 策略/workspace 绑定 | 无 | strategy_name, workspace_path |
| metric targets | 无 | 默认 calmar/sharpe/max_dd |
| 轮次控制 | 无 | cooldown, max_rounds, budget |
| 监控 | 无 | monitor_interval, drift detection |

**`/study start` = `/goal start` + 自动执行引擎。** `/goal start` 只记账，不触发自动执行。

### Chat（对话）

Chat 是系统的基础执行原语。一条用户消息触发一个 agent turn——LLM 推理、工具调用、返回结果。Chat 是**无状态的单轮交互**（在 AgentLoop 的单次 `run()` 中完成）。

### Workflow = DAG

Workflow 是 agent 执行的有向无环图（DAG）。每个节点是一个 agent，边定义数据流。例如 8-agent autoresearch pipeline：

```
researcher → data_quality → factor_analyst → strategist → portfolio_construction
                                                                    ↓
anti_overfit_analyst ← attribution_analyst ← risk_controller ← backtest
                                                                    ↓
                                                          backtest_diagnostics
```

### Agent = Chat 集合

每个 agent 是一个独立的 chat 实例。它：
- 拥有独立的消息上下文（自己的 AgentLoop）
- 只通过 DAG 边与其他 agent 交互（接收上游输出，传递下游输入）
- 按 DAG 顺序执行（串行链，或未来的并行层）

**Agent 不直接访问其他 agent 的内部状态**——只能通过 DAG 边传递结构化输出。

## 2. 层级关系

```
┌─────────────────────────────────────────────┐
│              Session（执行边界）              │
│  ┌─────────────────────────────────────────┐ │
│  │     Study / Goal（长程任务）             │ │
│  │  ┌───────────────────────────────────┐  │ │
│  │  │    Workflow = DAG（任务编排）      │  │ │
│  │  │  ┌─────┐  ┌─────┐  ┌─────┐      │  │ │
│  │  │  │Chat │→│Chat │→│Chat │→ ...   │  │ │
│  │  │  │Agent│  │Agent│  │Agent│        │  │ │
│  │  │  └─────┘  └─────┘  └─────┘      │  │ │
│  │  └───────────────────────────────────┘  │ │
│  └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

**自下而上的构建**：
- Chat 是原子单元（单轮 agent 交互）
- Workflow 是 Chat 的 DAG 编排（多 agent 序列化/并行化）
- Study/Goal 是 Workflow 的目标驱动封装（添加目标、验收、证据、预算）
- Session 是 Study/Goal 的执行边界（隔离不同研究方向）

## 3. 一个 Session = 一个活跃任务

**核心约束**：一个 session 同一时刻只能有一个正在执行的任务（study 或 goal）。

### 为什么？

1. **资源隔离**：不同 session 的研究互不干扰（不同 workspace、不同 LLM 调用、不同回测）
2. **状态清晰**：一个 session 的进度、指标、证据归属明确
3. **避免竞态**：同一 session 的多个并行任务会竞争 workspace 文件、LLM 配额、数据库锁
4. **用户心智模型**：一个 session = 一个研究方向，用户可以清晰地暂停/恢复/取消

### 实现方式

**创建时互斥**（supersede 模式）：

```python
# GoalStore.replace_goal() — 已有实现
# 创建新 goal 前，将同 session 的所有 CURRENT 目标标记为 SUPERSEDED
UPDATE goals SET status='superseded'
WHERE session_id=? AND status IN (active, paused, waiting_user, ...)

# StudyStore.create_study() — 新实现
# 创建新 study 前，将同 session 的所有活跃 study 标记为 CANCELLED
UPDATE studies SET execution_status='cancelled', completed_at=now, updated_at=now
WHERE session_id=? AND execution_status IN (queued, running, paused, monitoring)
```

**执行时互斥**（cooperative mutex）：

```python
# SessionService._processing_sessions — 已有实现
# session 的处理槽位由 chat 和 study 共享
is_session_processing(session_id) → bool
mark_session_processing(session_id, processing=True/False)

# StudyScheduler._run_one_study()
# 等待 chat 空闲 → claim slot → 执行 study → release slot
while session_service.is_session_processing(session_id):
    await asyncio.sleep(1)
session_service.mark_session_processing(session_id, True)
try:
    await executor.run()
finally:
    session_service.mark_session_processing(session_id, False)
```

### Goal 和 Study 的关系

**Goal 是超集，Study 是特化**。在当前实现中：

- `/goal start` → GoalStore.replace_goal() → 只记账，不执行
- `/study start` → GoalStore.replace_goal() + StudyStore.create_study() → 记账 + 自动执行

**两者共享同一个互斥约束**：一个 session 只能有一个活跃的 study（study 的创建会 supersede 旧 study）。goal 的 `_CURRENT_STATUSES` + unique index 已经强制每 session 单活跃 goal。

**交叉约束**（当前未实现，可选）：goal 和 study 之间的互斥。例如一个 session 有活跃 study 时，`/goal start` 应该报错或自动取消 study。这取决于产品决策——是否允许纯手动 goal 与自动 study 并存。

## 4. Chat 和 Study 的关系

**Study 是 Chat 的更高层抽象**。

| 维度 | Chat | Study (Goal) |
|------|------|-------------|
| 执行原语 | 单轮 agent turn | 多轮 autoresearch loop |
| 交互模式 | 用户 ↔ agent 实时对话 | agent 自主执行，用户间接控制 |
| 目标 | 无显式目标（隐含在对话中） | 显式目标 + 验收标准 |
| 持久化 | 消息历史（可选） | 目标 + 证据 + 审计（必须） |
| 执行控制 | 用户发送下一条消息 | 用户暂停/恢复/重定向 |

**Study 建立在 Chat 之上**：
- Study 的每一轮调用 `run_research_round` → `spawn_agent` → `AgentLoop.run()` → 底层是 chat 调用
- Study 的 directives（用户干预）通过修改 agent prompt 注入
- Study 的 SSE 事件通过 session 的 EventBus 发送到前端

**Study 和 Chat 共享处理槽位**：
- 研究轮次之间（cooldown 期间），session 空闲，chat 可以处理
- 研究轮次内（8-agent 执行期间），session 被锁定，chat 排队
- 这是 cooperative mutex，不是 preemption——不会中断正在执行的 agent turn

## 5. Workflow = DAG，Agent = Chat

### DAG 执行模型

```python
class WorkflowDAG:
    """Workflow 是 agent 执行的 DAG 定义。"""
    nodes: dict[str, AgentNode]  # agent_id → 配置
    edges: list[Edge]            # 上游 → 下游 的数据依赖
    layers: list[list[str]]      # 拓扑排序后的并行层

    def execution_order(self) -> list[list[str]]:
        """返回可并行执行的 agent 层级。"""
        # layer 0: [researcher]
        # layer 1: [data_quality]
        # layer 2: [factor_analyst]
        # ...
        # layer 7: [backtest_diagnostics]
```

### Agent = 独立 Chat

每个 agent 是一个 `AgentLoop` 实例：

```python
agent = AgentLoop(
    llm_config=...,           # LLM 配置
    tool_registry=...,        # 可用工具集
    system_prompt=...,        # agent 专属系统提示（从 .prompts/{role}.md 加载）
    workspace=...,            # 工作空间
    session_id=...,           # 绑定的 session（用于 SSE 事件）
    max_iterations=25,        # ReAct 循环上限
)
result = agent.run(task_prompt)  # 执行
```

**Agent 只通过 DAG 边交互**：
- 输入：上游 agent 的结构化输出 + goal context
- 输出：结构化结果（传递给下游 agent）
- 无共享状态：每个 agent 有独立的消息上下文

### 当前实现（8-agent 串行链）

`run_research_round()` 是串行实现（非 DAG）：

```python
researcher_out = spawn("researcher", current_state)
dq_out = spawn("data_quality", researcher_out)
factor_out = spawn("factor_analyst", dq_out)
strategist_out = spawn("strategist", factor_out)
portfolio_out = spawn("portfolio_construction", strategist_out)
metrics = run_backtest_script(strategy)
risk_out = spawn("risk_controller", metrics)
attribution_out = spawn("attribution_analyst", risk_out)
anti_overfit_out = spawn("anti_overfit_analyst", attribution_out)
diagnostics_out = spawn("backtest_diagnostics", anti_overfit_out)
```

**这是 DAG 的特例**（线性链 = 每层一个节点）。未来可扩展为真正的 DAG（并行层、条件分支）。

## 6. 状态管理分层

```
┌──────────────────────────────────────────────────┐
│  Ledger Layer（GoalStore）                       │
│  - goal status / criteria / evidence / audit     │
│  - 纯记账，不驱动执行                             │
│  - 每 session 单活跃 goal（unique index）          │
├──────────────────────────────────────────────────┤
│  Execution Layer（StudyStore + Executor）         │
│  - study execution_status / round / metrics      │
│  - 驱动 autoresearch loop                        │
│  - 每 session 单活跃 study（supersede）            │
├──────────────────────────────────────────────────┤
│  Processing Layer（SessionService）               │
│  - _processing_sessions 共享槽位                   │
│  - chat 和 study 互斥                              │
│  - cooperative mutex，非 preemption                │
└──────────────────────────────────────────────────┘
```

**三层各自独立，通过 session_id 关联**：
- GoalStore 管"要做什么"
- StudyStore + Executor 管"怎么做"和"做到什么程度"
- SessionService 管"谁在执行"和"执行槽位分配"

## 7. 待解决的设计决策

### 7.1 Goal 和 Study 的交叉互斥

当前：goal 和 study 各自独立 supersede 同类型的旧任务，但不检查对方。

**选项 A**：goal 和 study 互斥（一个 session 只能有一个活跃的 goal 或 study）
- 优点：语义清晰，一个 session = 一个任务
- 缺点：`/goal start` 需要检查 study 状态，增加耦合

**选项 B**：study 包含 goal（study 创建时自动创建 goal，goal 不单独存在）
- 优点：简化模型，study 是唯一的一等任务
- 缺点：纯手动 goal（无自动执行）无法存在

**选项 C**：当前状态（不处理）
- 优点：最小改动
- 缺点：同一 session 可能同时有活跃 goal 和活跃 study

**建议**：选项 B（study 是唯一的一等任务，goal 是 study 的内部组件）。这与当前实现最接近——`/study start` 已经同时创建 goal 和 study。`/goal start` 可以改为 `/goal start` → 创建 study 但不提交到 scheduler（手动模式）。

### 7.2 Workflow 的 DAG 化

当前：8-agent 串行链（`run_research_round` 硬编码）。

**选项 A**：保持串行链
- 优点：简单，当前够用
- 缺点：扩展性差，无法并行

**选项 B**：DAG 化（YAML 配置 + WorkflowController）
- 优点：灵活，支持并行层、条件分支、子工作流
- 缺点：实现复杂度高（已有 goal-workflow-design.md 设计）

**建议**：选项 B（参考 goal-workflow-design.md 的 DAG 设计）。但当前阶段先保持串行链，DAG 化作为后续 Phase。

### 7.3 Study 的 monitor 模式

当前（Phase 3）：study 完成后进入 MONITORING 状态，定期重新回测检查指标漂移。

**问题**：MONITORING 状态算"活跃"吗？如果是，它会阻塞 session 的新 study 创建。

**建议**：MONITORING 不算"执行中"——它只是定期检查，不占用处理槽位。从 `ACTIVE_EXECUTION_STATUSES` 中移除 `MONITORING`，让新 study 可以在 monitor 期间创建。

---

*待确认以上设计决策后，再进入实施阶段。*
