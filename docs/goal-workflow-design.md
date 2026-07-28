# Goal Workflow 子系统设计 (P3-e)

> 适配自 vibe-trading-ai 0.1.11 (MIT License, HKUDS)。
> 见 `docs/vibe-trading-credits.md` 获取完整归属列表。

## 1. 目的

**Goal Workflow** 是一个 YAML 配置驱动的子系统，用于控制 agent 在 goal 模式下的行为。它将结构化的研究流程定义在 YAML 文件中，由 `WorkflowController` (DAG 调度器) 驱动执行，自动收集 evidence，自动完成 audit。

解决的问题：

- **缺乏结构**：当前 goal 模式只有一个简单的 agent loop + continuation prompt，没有明确的研究流程
- **过程不可控**：LLM 自由发挥，无法保证关键步骤被执行
- **evidence 录入繁琐**：用户需要手动调用 `add_goal_evidence`
- **完成审计重**：必须手动管理 audit row

## 2. 架构

### 2.1 三层架构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Goal Workflow Engine (新增)                        │
│  core/goal/workflow.py                                       │
│  - GoalWorkflowRunner: 加载 YAML → 创建 Goal → 执行 DAG     │
│  - GoalWorkflowState: 跟踪每个 agent 的执行状态              │
│  - GoalEvidenceCollector: agent 输出 → auto append_evidence │
│  - GoalAutoComplete: DAG 完成后自动 audit + complete         │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Workflow 层 (已有)                                 │
│  core/workflow/controller.py  WorkflowController            │
│  core/workflow/dag.py  topological_layers                   │
│  core/workflow/prompt.py  PromptBuilder                     │
├─────────────────────────────────────────────────────────────┤
│  Layer 1: Agent 层 (已有)                                    │
│  core/agent/loop.py  AgentLoop                              │
│  core/agent/role_factory.py  build_agent_loop               │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
用户: /goal start "研究动量因子" --workflow factor_research
  │
  ├─ workflow_config.load_goal_workflow("factor_research")
  │   └─ 解析 YAML → GoalWorkflowConfig
  │
  ├─ GoalWorkflowRunner.start("研究动量因子")
  │   │
  │   ├─ GoalStore.replace_goal(...) → 创建 goal (status=active)
  │   │
  │   ├─ for layer in topological_layers(dag):
  │   │   │
  │   │   ├─ 检查暂停标志
  │   │   ├─ 应用分支条件
  │   │   ├─ 并行执行层内 agents
  │   │   │   └─ for agent in layer:
  │   │   │       ├─ _build_prompt(agent) = goal_context + base + upstream_outputs
  │   │   │       ├─ agent_runner(agent, prompt, tools) → result
  │   │   │       └─ evidence_collector.collect(result) → GoalStore.append_evidence
  │   │   │
  │   │   └─ 检查是否所有 criteria 覆盖 → auto_complete()
  │   │
  │   └─ return goal_id
  │
  └─ TUI 显示 GoalPanel 更新进度
```

## 3. 数据模型

### 3.1 GoalWorkflowConfig

```python
@dataclass
class GoalWorkflowConfig:
    name: str
    description: str
    version: str = "1.0"
    goal: GoalWorkflowGoalConfig
    agents: list[GoalAgentConfig]
    dag: dict[str, list[str]]  # key: [upstream_deps]
    completion: CompletionConfig
    branches: list[BranchConfig] = []
```

### 3.2 GoalAgentConfig

每个 agent 节点：

```python
@dataclass
class GoalAgentConfig:
    id: str
    prompt_file: str                # 模板路径（相对 templates/）
    tools: list[str] = []           # 工具白名单
    input_from: list[str] = []      # 上游依赖列表
    evidence_criterion: int = 0     # 映射到第 N 个 criterion
    timeout: int = 120              # 秒
    max_retries: int = 3
    condition: str | None = None    # 条件表达式（Phase 2）
```

### 3.3 CompletionConfig

```python
@dataclass
class CompletionConfig:
    mode: str = "auto"  # auto | manual | lite
    auto_audit: bool = True
    require_all_evidence: bool = True
```

### 3.4 BranchConfig (Phase 2)

```python
@dataclass
class BranchConfig:
    condition: str  # 条件表达式
    action: str     # skip | retry | redirect
    target: str     # 目标 agent_id
    reason: str = ""
```

### 3.5 GoalWorkflowState

```python
@dataclass
class GoalWorkflowState:
    status: str = "idle"  # idle | running | paused | completed | error | cancelled
    current_layer: int = 0
    paused: bool = False
    pause_layer: int = -1
    agent_statuses: dict[str, str] = {}  # agent_id → pending/running/success/error/skipped
    agent_errors: dict[str, str] = {}
    evidence_count: int = 0
    start_time: float = 0.0
```

### 3.6 GoalRecord 扩展

```python
@dataclass
class GoalRecord:
    # ... 原有字段 ...
    workflow_id: str | None = None  # 绑定到 goal 的 workflow config 名
```

数据库新增 `workflow_id` 列（带 migration）。

## 4. YAML 格式

### 4.1 完整格式

```yaml
name: goal_factor_research
description: 因子研究工作流 — 从因子定义到风险评审的完整流程
version: "1.0"

goal:
  default_criteria:
    - "定义因子逻辑和标的池"
    - "收集历史数据并回测因子表现"
    - "分析因子衰减和稳健性"
    - "记录风险提示和非建议边界"
  risk_tier: research_general

agents:
  - id: researcher
    prompt_file: .prompts/researcher.md
    tools: [read_file, search_web]
    input_from: []
    evidence_criterion: 0
    timeout: 120
    max_retries: 2

  - id: data_quality
    prompt_file: .prompts/data_quality.md
    tools: [read_file]
    input_from: [researcher]
    evidence_criterion: 1
    timeout: 120
    max_retries: 2

  - id: factor_analyst
    prompt_file: .prompts/factor_analyst.md
    tools: [compute_factor, run_backtest]
    input_from: [researcher, data_quality]
    evidence_criterion: 2
    timeout: 300
    max_retries: 3

  - id: risk_reviewer
    prompt_file: .prompts/risk_controller.md
    tools: [run_backtest]
    input_from: [factor_analyst]
    evidence_criterion: 3
    timeout: 180
    max_retries: 2

dag:
  researcher: []
  data_quality: [researcher]
  factor_analyst: [researcher, data_quality]
  risk_reviewer: [factor_analyst]

completion:
  mode: auto
  auto_audit: true
  require_all_evidence: true

branches:  # 可选，Phase 2
  - condition: "factor_analyst.output.sharpe < 0.3"
    action: skip
    target: risk_reviewer
    reason: "Sharpe 太低，跳过风险评审"
```

### 4.2 DAG 约定

**关键约定**：YAML 使用 `key: [upstream_deps]` 语义（节点依赖列表中的节点）。

```yaml
dag:
  a: []           # a 无上游依赖
  b: [a]          # b 依赖 a
  c: [a, b]       # c 依赖 a 和 b
```

执行顺序：
- Layer 0: `a`
- Layer 1: `b`
- Layer 2: `c`

Runner 内部会反转邻接表传给 `topological_layers()`（后者使用 `key: [downstream]` 约定）。

### 4.3 文件路径

搜索顺序：
1. 显式路径（如果传入的是文件路径）
2. `core/swarm/presets/goal_{name}.yaml`
3. `core/swarm/presets/{name}.yaml`
4. `~/.quantnodes-research/workflows/{name}.yaml`

## 5. 核心组件

### 5.1 GoalWorkflowRunner

```python
class GoalWorkflowRunner:
    def __init__(
        self,
        config: GoalWorkflowConfig,
        session_id: str,
        *,
        agent_runner: Callable | None = None,  # 可注入用于测试
    ) -> None: ...

    async def start(self, objective: str) -> str:
        """启动 workflow: 创建 goal → 执行 DAG → 返回 goal_id"""

    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def get_progress(self) -> dict: ...

    async def _execute_dag(self) -> None:
        """按层执行 DAG，每层并行执行 agents"""

    async def _execute_layer(self, layer: list[str], layer_idx: int) -> None:
        """并行执行一层的所有 agents"""

    async def _execute_agent(self, agent_id: str, layer_idx: int) -> None:
        """执行单个 agent，retry，收集 evidence"""

    def _build_prompt(self, agent_id: str) -> str:
        """构建 prompt: goal_context + base + upstream_outputs"""

    def _check_all_criteria_covered(self) -> bool: ...
    async def _auto_complete(self) -> None: ...
```

### 5.2 GoalEvidenceCollector

```python
class GoalEvidenceCollector:
    def __init__(self, session_id: str, goal_id: str): ...

    def collect(
        self,
        agent_id: str,
        result: dict[str, Any],
        criterion_idx: int,
    ) -> int:
        """将 agent 输出自动收集为 goal evidence。
        Returns evidence count added (0 or 1)."""
```

### 5.3 workflow_config.py

```python
def load_goal_workflow(name_or_path: str, *, base_dir: Path | None = None) -> GoalWorkflowConfig:
    """从 YAML 加载 workflow 配置，自动验证 DAG 完整性"""

def list_goal_workflows() -> list[dict[str, str]]:
    """列出所有可用的 workflow preset"""
```

验证内容：
- `agents` 非空
- `dag` 非空
- DAG 中所有引用的 agent 都在 `agents` 中
- DAG 无环（调用 `validate_dag()`）
- `evidence_criterion` 索引有效

## 6. 与 Goal 子系统的集成

### 6.1 GoalStore 扩展

```python
# GoalStore.replace_goal 新增参数
def replace_goal(
    self,
    *,
    session_id: str,
    objective: str,
    criteria: list[str],
    workflow_id: str | None = None,  # 新增
    ...
) -> GoalRecord: ...
```

数据库 schema 新增列：
```sql
ALTER TABLE goals ADD COLUMN workflow_id TEXT;
```

### 6.2 与 GoalPanel 的集成 (Phase 2)

GoalPanel 需要扩展显示 workflow 进度：

```
┌─ GoalPanel ────────────────────────────────────────────────┐
│ 🎯 因子研究: 动量因子在 A 股的有效性                        │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 75% (3/4)      │
│ ✔ 1. 定义因子逻辑和标的池  [researcher ✓]                  │
│ ✔ 2. 收集历史数据并回测    [data_quality ✓]                │
│ ✔ 3. 分析因子衰减和稳健性  [factor_analyst ✓]              │
│ ○ 4. 记录风险提示          [risk_reviewer ⏳ 运行中]       │
│ 📎 6 evidence  │  Ctrl+G 暂停  │  Layer 3/4               │
└────────────────────────────────────────────────────────────┘
```

### 6.3 与 slash_goal.py 的集成 (Phase 2)

```bash
# 启动带 workflow 的 goal
/goal start "研究动量因子" --workflow factor_research

# 列出可用 workflow
/goal workflows
```

### 6.4 与 session.py 的集成 (Phase 2)

Ctrl+G 既可以暂停 goal continuation，也可以暂停 workflow 执行。

## 7. 执行语义

### 7.1 Prompt 构建

每个 agent 的 prompt 包含三部分：

```python
prompt = goal_context + base_prompt + upstream_outputs
```

- **goal_context**：从 `format_goal_context(snapshot)` 获取，包含 goal_id、criteria、evidence_count
- **base_prompt**：从 `prompt_file` 加载（相对 `templates/` 目录）
- **upstream_outputs**：每个上游 agent 的 `result["answer"]`，截断到 1500 字符

### 7.2 并行执行

层内 agents 通过 `asyncio.gather()` 并行执行。每个 agent 是独立的 `asyncio` task，失败不影响其他 agent。

### 7.3 重试机制

```python
for attempt in range(max_retries + 1):
    try:
        result = await self._run_agent(agent_config, prompt, layer_idx)
        # 成功 → 收集 evidence → break
    except asyncio.TimeoutError:
        # 超时 → 重试（最多 max_retries 次）
    except Exception:
        # 其他错误 → 重试
```

每次重试间隔 1.0 秒。

### 7.4 自动完成

```python
if self._check_all_criteria_covered():
    await self._auto_complete()
    return
```

`_check_all_criteria_covered()` 遍历所有 required criteria，确认每个都有 evidence。

`_auto_complete()` 根据 `completion.mode`：
- `auto`：构建 audit rows（每个 criterion 一个 satisfied row），调用 `update_status(COMPLETE, audit=...)`
- `lite`：调用 `complete_lite()`（仅 evidence 覆盖，不要求 audit row）

### 7.5 暂停/恢复

```python
def pause(self):
    self._state.paused = True

async def continue_after_pause(self):
    if not self._state.paused:
        return
    self._state.paused = False
    # 从暂停的层继续执行
    for layer_idx in range(self._state.pause_layer, ...):
        if self._state.paused:
            self._state.pause_layer = layer_idx
            return
        # ... 执行该层
```

## 8. 与 vibe-trading 的对比

### 8.1 复用现有架构

Goal Workflow 子系统**复用**了 vibe-trading 的 workflow 架构：

| vibe-trading 组件 | Goal Workflow 用法 |
|---|---|
| `WorkflowController` | DAG 调度（通过 Runner 间接调用） |
| `topological_layers()` | 计算执行层（反转邻接表后调用） |
| `validate_dag()` | YAML 加载时验证 |
| `PromptBuilder` | 未来可复用（Phase 2） |
| `AgentValidator` | 未来可复用（Phase 2） |

### 8.2 新增组件

| 新增组件 | 用途 |
|---|---|
| `GoalWorkflowConfig` | YAML 配置模型 |
| `GoalWorkflowState` | 执行状态跟踪 |
| `GoalWorkflowRunner` | 执行引擎 |
| `GoalEvidenceCollector` | 自动 evidence 收集 |
| `load_goal_workflow()` | YAML 加载器 |
| `list_goal_workflows()` | preset 列表 |

### 8.3 与 SwarmRuntime 的区别

| 维度 | SwarmRuntime | Goal Workflow |
|---|---|---|
| 用途 | 多 agent 协作研究 | Goal 驱动的研究流程 |
| 配置 | 通用 YAML preset | Goal-specific YAML preset |
| 状态存储 | RunStore | GoalStore |
| 完成机制 | 显式 `decide()` | Auto-complete on criteria coverage |
| 并行执行 | ThreadPoolExecutor | asyncio.gather |
| Evidence 收集 | 手动 | 自动（每个 agent 输出 → criterion） |

## 9. 文件清单

### 新增

```
src/strategy_research/core/goal/
  workflow.py            (~400 行) — Runner + State + Config
  workflow_config.py     (~150 行) — YAML 加载 + 验证

src/strategy_research/core/swarm/presets/
  goal_factor_research.yaml  (~60 行) — 第一个 preset

tests/
  test_goal_workflow.py  (~250 行) — 22 个测试
```

### 修改

```
src/strategy_research/core/goal/
  __init__.py    — 导出 workflow 模块
  models.py      — GoalRecord.workflow_id
  store.py       — workflow_id 列 + migration + replace_goal 参数

tests/
  test_goal_models.py  — 字段计数 21→22
```

### Phase 2 待做

```
src/strategy_research/cli/commands/slash_goal.py
  - cmd_start 增加 --workflow 参数
  - cmd_workflows 列出可用 workflow

src/strategy_research/cli/tui/widgets/goal_panel.py
  - 显示 workflow 进度（layer 信息、agent 状态图标）

src/strategy_research/cli/tui/session.py
  - Ctrl+G 暂停 workflow 执行

src/strategy_research/core/goal/templates.py
  - 合并 GoalTemplate 与 GoalWorkflowConfig
```

## 10. 测试覆盖

### 10.1 单元测试 (test_goal_workflow.py — 22 tests)

| 测试类 | 测试数 | 覆盖内容 |
|---|---|---|
| TestGoalWorkflowConfig | 4 | Config / AgentConfig / CompletionConfig / BranchConfig |
| TestGoalWorkflowState | 4 | 默认状态 / set_agent_status / error / get_summary |
| TestGoalEvidenceCollector | 3 | 空结果 / 短结果 / 无效索引 |
| TestGoalWorkflowRunner | 6 | init / progress / layers / agent_config / pause / goal_context |
| TestYAMLLoading | 3 | 加载现有 preset / 加载不存在 / 列表 |
| TestDAGValidation | 2 | 无环 DAG / 有环检测 |

### 10.2 集成测试

未来需要（Phase 2）：
- 完整 workflow 执行（mock agent_runner）
- evidence 收集 → criterion 覆盖 → auto-complete 链路
- 暂停/恢复跨层执行

## 11. 限制与未来工作

### 11.1 当前限制

- **agent_runner 必须注入**：未集成 AgentLoop，需要 stub 或手动实现
- **无 GUI 进度反馈**：GoalPanel 还未接入 workflow 状态
- **无分支逻辑**：BranchConfig 已定义但 `_evaluate_condition` 是 stub
- **单一 evidence criterion**：每个 agent 只能映射到 1 个 criterion
- **无子 workflow**：不支持 workflow 嵌套

### 11.2 Phase 2 计划

- [ ] 集成 AgentLoop 作为默认 agent_runner
- [ ] GoalPanel 显示 workflow 进度（每个 agent 状态图标）
- [ ] slash_goal.py 加 `--workflow` 参数
- [ ] session.py Ctrl+G 暂停 workflow
- [ ] 表达式求值（简单 DSL：`output.field < value`）
- [ ] 4 个额外 preset（market_analysis / risk_assessment / strategy_review / portfolio_review）

### 11.3 Phase 3 计划

- [ ] 子 workflow 支持（parent_goal_id 已有字段）
- [ ] 工作流进度 checkpoint + 恢复
- [ ] 可视化工作流编辑器
- [ ] Workflow 性能监控（每个 agent 耗时）
- [ ] 与 Hook 系统集成

## 12. 参考资料

- `docs/workflow-design.md`: vibe-trading workflow 完整设计
- `docs/goal-design.md`: Goal 子系统设计
- `docs/enhancement.md`: 借鉴方案（P3-d 提到 AgentLoop.run 集成）
- `core/workflow/controller.py`: WorkflowController 实现
- `core/workflow/dag.py`: DAG 算法
- `core/swarm/presets/full_pipeline.yaml`: 现有 swarm preset 格式