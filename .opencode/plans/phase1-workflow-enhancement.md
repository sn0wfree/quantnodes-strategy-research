# Phase 1 实施计划：增强 Goal Workflow（Study 特性沉淀）

## 总览

将 Study 的5个关键功能迁移到 Goal Workflow 中，为后续替换 Study 执行引擎做准备。

**优先级**：Session mutex > Budget > Metric targets > Directive > Monitor

---

## 1.1 Session 串行执行 + Cooperative Mutex

**目标**：Goal Workflow 启动时与 chat 互斥，同一 session 同时只有一个任务在执行。

### 改动文件

| 文件 | 改动 |
|------|------|
| `core/goal/workflow.py` | `GoalWorkflowRunner.start()` 添加 session slot check/claim/release |
| `core/swarm/runtime.py` | `execute()` 添加 `session_service` 参数 |
| `api/routers/workflow.py` | 传递 session_service 到 runner |

### 实现方案

```python
# GoalWorkflowRunner.start() 中添加
async def start(self, objective, *, session_id=None, ...):
    # 1. 等待 session slot
    if session_id and self._session_service:
        while self._session_service.is_session_processing(session_id):
            await asyncio.sleep(0.25)
        self._session_service.mark_session_processing(session_id, True)
    
    try:
        # 2. 执行 workflow
        result = await self._runtime.execute(preset, ...)
    finally:
        # 3. 释放 slot
        if session_id and self._session_service:
            self._session_service.mark_session_processing(session_id, False)
```

### 测试

- `test_workflow_session_mutex.py`:
  - test_workflow_blocks_chat
  - test_chat_blocks_workflow
  - test_workflow_releases_slot_on_complete
  - test_workflow_releases_slot_on_error

---

## 1.2 Budget 记账

**目标**：Goal Workflow 支持 token/turn/time 全局预算，超支时停止。

### 改动文件

| 文件 | 改动 |
|------|------|
| `core/swarm/runtime.py` | `execute()` 中累积 budget，`SwarmPreset` 添加 budget 字段 |
| `core/goal/workflow.py` | `GoalWorkflowConfig` 添加 budget 字段 |
| `core/goal/workflow_hook.py` | `should_stop()` 检查 budget |

### 实现方案

```python
# SwarmPreset 添加
@dataclass
class SwarmPreset:
    ...
    budget_token: int | None = None
    budget_turn: int | None = None
    budget_time_seconds: float | None = None

# SwarmRuntime.execute() 中
budget = {"token": 0, "turn": 0, "time": 0.0}
start_time = time.perf_counter()

for layer in layers:
    # 执行前检查 budget
    if self._budget_exceeded(budget, preset):
        break
    
    layer_results = self._execute_layer(layer, ...)
    
    # 累积 budget
    for agent_id, result in layer_results.items():
        budget["turn"] += 1
        budget["time"] = time.perf_counter() - start_time
        # token 由 agent 执行器报告
```

### 测试

- `test_workflow_budget.py`:
  - test_budget_time_exceeded
  - test_budget_turn_exceeded
  - test_budget_not_exceeded
  - test_budget_none_means_unlimited

---

## 1.3 Metric Target 检查

**目标**：Goal Workflow 支持数值指标比较（如 calmar >= 0.5），达标时自动完成。

### 改动文件

| 文件 | 改动 |
|------|------|
| `core/goal/workflow.py` | `GoalWorkflowConfig.completion` 添加 `metric_targets` |
| `core/goal/workflow_hook.py` | `on_layer_complete()` 中检查 metric targets |
| `core/study/executor.py` | 复用 `meets_metric_targets()` 函数 |

### 实现方案

```python
# GoalWorkflowConfig.completion 添加
@dataclass
class CompletionConfig:
    ...
    metric_targets: list[dict] | None = None  # [{"name": "calmar", "op": ">=", "value": 0.5}]

# GoalWorkflowHook.on_layer_complete() 中
def on_layer_complete(self, layer_idx, layer_results):
    # 1. 收集证据（已有）
    self._collect_evidence(layer_results)
    
    # 2. 检查 metric targets（新增）
    if self._config.completion.metric_targets:
        metrics = self._extract_metrics(layer_results)
        if meets_metric_targets(metrics, self._config.completion.metric_targets):
            self._auto_complete()
            return
    
    # 3. 检查证据覆盖（已有）
    if self._check_all_criteria_covered():
        self._auto_complete()
```

### 测试

- `test_workflow_metric_targets.py`:
  - test_metric_targets_met_triggers_complete
  - test_metric_targets_not_met_continues
  - test_no_metric_targets_uses_evidence_only
  - test_extract_metrics_from_agent_output

---

## 1.4 Directive 注入

**目标**：Goal Workflow 支持用户在执行中途注入指令，影响后续 agent 行为。

### 改动文件

| 文件 | 改动 |
|------|------|
| `core/goal/workflow.py` | `GoalWorkflowRunner` 添加 directive store |
| `core/goal/workflow_hook.py` | `on_layer_start()` 中注入 directive 到 prompt |
| `api/routers/workflow.py` | 添加 `POST /workflow/{goal_id}/directive` |
| `core/swarm/runtime.py` | `_execute_agent()` 支持 directive 参数 |

### 实现方案

```python
# GoalWorkflowRunner 添加
class GoalWorkflowRunner:
    def __init__(self, ...):
        ...
        self._directives: list[dict] = []  # [{"content": "...", "created_at": "..."}]
    
    def add_directive(self, content: str) -> None:
        self._directives.append({
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    
    def consume_directives(self) -> str | None:
        if not self._directives:
            return None
        text = "\n".join(d["content"] for d in self._directives)
        self._directives.clear()
        return text

# GoalWorkflowHook.on_layer_start() 中
def on_layer_start(self, layer_idx, layer_agents):
    directives = self._runner.consume_directives()
    if directives:
        # 注入到本层所有 agent 的 prompt
        for agent_id in layer_agents:
            self._inject_directive(agent_id, directives)
```

### 测试

- `test_workflow_directives.py`:
  - test_add_directive
  - test_consume_directives_clears
  - test_directive_injected_into_agent_prompt
  - test_no_directives_no_injection

---

## 1.5 Monitor 模式

**目标**：Goal Workflow 完成后支持定期回测检查，检测策略漂移。

### 改动文件

| 文件 | 改动 |
|------|------|
| `core/goal/workflow.py` | `GoalWorkflowRunner` 添加 `_monitor_background()` |
| `core/goal/workflow_hook.py` | `on_complete()` 启动 monitor task |
| `core/swarm/runtime.py` | 无改动 |

### 实现方案

```python
# GoalWorkflowRunner 添加
class GoalWorkflowRunner:
    def __init__(self, ...):
        ...
        self._monitor_task: asyncio.Task | None = None
        self._monitor_interval: int | None = None
    
    async def _monitor_background(self) -> None:
        """Post-completion drift detection."""
        while True:
            await asyncio.sleep(self._monitor_interval)
            try:
                metrics = await asyncio.to_thread(
                    run_backtest_script, self._workspace, self._strategy, action="monitor"
                )
                if not meets_metric_targets(metrics, self._metric_targets):
                    self._state.status = "needs_refresh"
                    self._emit("workflow_drift_detected", {...})
                    return
            except Exception as exc:
                logger.warning("monitor check failed: %s", exc)

# GoalWorkflowHook.on_complete() 中
def on_complete(self):
    if self._runner._monitor_interval:
        self._runner._monitor_task = asyncio.create_task(
            self._runner._monitor_background()
        )
```

### 测试

- `test_workflow_monitor.py`:
  - test_monitor_launches_after_complete
  - test_monitor_detects_drift
  - test_monitor_no_drift_continues
  - test_monitor_not_launched_when_interval_none

---

## 实施顺序

```
1.1 Session mutex  →  验证: workflow 与 chat 互斥
    ↓
1.2 Budget         →  验证: 超支时 workflow 停止
    ↓
1.3 Metric targets →  验证: 指标达标时自动完成
    ↓
1.4 Directive      →  验证: 用户指令注入到 agent
    ↓
1.5 Monitor        →  验证: 完成后漂移检测
```

## 测试策略

每个功能独立测试，最后集成测试：

1. **单元测试**：每个功能的独立行为
2. **集成测试**：多个功能组合（如 budget + metric targets）
3. **回归测试**：确保现有 Goal Workflow 功能不受影响

## 文件影响汇总

| 文件 | 新增行 | 修改行 | 删除行 |
|------|--------|--------|--------|
| `core/goal/workflow.py` | ~80 | ~20 | 0 |
| `core/goal/workflow_hook.py` | ~60 | ~15 | 0 |
| `core/swarm/runtime.py` | ~40 | ~10 | 0 |
| `api/routers/workflow.py` | ~30 | ~5 | 0 |
| 测试文件 x5 | ~200 | 0 | 0 |
| **合计** | ~410 | ~50 | 0 |

---

*确认后进入实施阶段。*
