# Phase Engine 重构计划 — 步骤再评估（勘误版）

> 日期：2026-10-08 · 基线：commit `3eea546`
> 性质：对 `docs/architecture-harness-recommendations-20261008.md` §1.3 的**勘误 + 步骤重评**。
> 方法：读真实代码后修正口头方案（原方案有三处与代码不符）。

## 1. 勘误：三处与代码不符的判断

原文档 §1.3 与前次口头讨论中的 `EngineProtocol` 方案基于印象，读码后修正如下。

### 勘误 1：分派点不在 runner，在 phase_engine

| 原判断 | 事实 |
|---|---|
| runner 同时认识两个引擎，抽 Protocol 让 runner 只依赖接口 | **runner 不认识引擎**——`runner._run_one_round_impl` 只调 `run_round_phases`（`runner.py:735`，局部 import）；分派在 **`phase_engine.py:128-141`** |

### 勘误 2：双引擎不是并列可替换，是 if/else + 局部变量 shim 约定

分派逻辑（`phase_engine.py:128-141`）：

```python
engine = getattr(study, "engine", None) or "phases"      # 默认 phases
if os.environ.get("SR_STUDY_DAG_ENGINE") == "1" and engine == "phases":
    engine = "langgraph"                                  # env 遗留开关
if engine == "dag":
    engine = "langgraph"                                  # 'dag' 是假选项，静默映射
if engine == "langgraph":
    lg_result = runner._run_round_via_langgraph(...)      # 分支 A
    # line 159-205: 手工 shim 出 phases 分支的变量名
else:
    # 分支 B: run_researcher_phase → execution → evaluation
```

关键事实：
- 两分支靠**同名局部变量**（`researcher_output` / `metrics` / `eval_result` / `exec_result` / `captured_predictions`）衔接 line 207 之后的**共享 finalization**
- langgraph 分支需要 ~20 行 shim 才能让 finalization 不 AttributeError（`phase_engine.py:159-205`）
- `engine='dag'`：store/API 仍接受，但**无 executor 存在**，静默映射为 langgraph（`phase_engine.py:137-141` 注释自认）
- `SR_STUDY_DAG_ENGINE` 环境变量是另一个遗留开关

### 勘误 3：`_run_loop` 复杂度 32 的来源是生命周期控制流，不是引擎分派

`runner.py:362-570` 的 32 分支来自：
- 主循环内的 cancel 分支（含 ARCHIVED 状态子判断）
- pause 分支（含「暂停中被取消」子判断）
- HITL resume 巨块（`runner.py:490-570`：run_dir 重建、state 重建、`_resume_round_langgraph` 调用、三种失败路径）
- budget / 终止判断

**引擎分派完全不在 `_run_loop` 里。**

## 2. 新发现：langgraph 分支 claims 被清空（疑似真 bug）

`phase_engine.py` claims 链路：

| 行 | 分支 | 代码 | 问题 |
|---|---|---|---|
| 190 | langgraph | `captured_predictions = _capture_round_claims(...)` | 捕获真值 ✓ |
| **205** | langgraph | `captured_predictions = {}` | **立即清空** ✗（相邻注释只解释 `eval_result`/`exec_result` shim，未解释此行） |
| 238 | phases | `captured_predictions = _capture_round_claims(...)` | 真值，**不清空** ✓ |
| 317-320 | 共享 | `_record_journal_and_regression(..., predictions=captured_predictions)` | langgraph 传 `{}`，phases 传真值 |

**影响假说**：langgraph 引擎下 journal phase-B（backfill）拿到空 predictions，与 phase-A（`_capture_round_claims` 内部 append）写入的内容不一致；phases 引擎正常。

**为什么现有测试没抓到**：`tests/test_langgraph_parity.py`（17 个测试）只验证 **output schema** 等价（docstring 自述），journal 是 side-effect 不在 schema 内。

**Step 0 将用新增测试验证此假说**（只新增，不改生产代码，先出证据）。

## 3. 修正后的步骤（替代原「EngineProtocol」）

| Step | 动作 | 风险 | 护栏 | 工期 |
|---|---|---|---|---|
| **0** | 新增 langgraph 分支 journal/predictions 断言测试 → 确认 line 205 是否真 bug → 若确认，**仅删/改该行**（最小改动） | 零（先测试后一行改动） | 测试本身 | 半天 |
| **1** | 定义 `RoundOutcome` dataclass：两分支各自构造返回值，finalization 只消费 outcome——把局部变量约定变**显式类型契约**。**对外签名 `run_round_phases(...)` 不变** | 中低（动 806 行函数内部） | `test_langgraph_parity.py` 17 测直接适用 | 1-2 天 |
| **2** | 分派查表化 `{"phases": ..., "langgraph": ...}`；同步收口 `engine='dag'` 假选项与 `SR_STUDY_DAG_ENGINE` env 遗留 | 低（纯移动，Step 1 契约已立） | Step 1 契约 | 半天-1 天 |
| **3** | `_run_loop` 生命周期状态机化：`cancel/pause/resume/terminate` 抽 `StudyLifecycle`，主循环变 `while not terminal: transition()`。**方向已修正：不是 engine step 化** | 中高（核心循环） | 12,366 全量 + eval 子集 | 2-3 天 |

### 逻辑链条

```
Step 0  用活证据（line 205）证明「shim 约定会藏 bug」
   ↓    同时堵住已知漏洞
Step 1  RoundOutcome = 把 shim 约定显式化为类型契约
   ↓    分支体缩短（C901 24 → 目标 ≤15）
   ↓    parity 测试从「碰巧过 schema」变「类型上难偏」
Step 2  契约已立 → 分派只认「谁产出 RoundOutcome」→ 查表自然成立
   ↓    此时删 'dag'/env 假选项是安全的
Step 3  分派与契约稳定后 → 才动 _run_loop 生命周期
```

**支点是 Step 1**：没有 `RoundOutcome`，Step 2 只是变量约定的搬家，Step 3 建立在流沙上。

### 护栏缺口（Step 1 验收时补）

parity 测试覆盖 schema、不覆盖 side-effect——Step 0 的 bug 正从这个缺口溜过。Step 1 验收应追加：**两分支跑完后 journal / state_store 落盘 diff 一致**。

## 4. 原文勘误指针

`docs/architecture-harness-recommendations-20261008.md` §1.3 的「`EngineProtocol`（`start_round`/`resume`/`snapshot`）」接口草案与「runner 认识双引擎」的前提**均已作废**，以本文档 §1-§3 为准；该条的优先级判断（study 心脏、先低风险后高风险、当前有护栏）仍然成立。

## 5. 执行约束

- **他人代码最小触碰**：Step 0 只新增测试 + 最多一行生产代码改动；Step 1-3 每步单独评审后再动
- 每步独立 commit，可单独回滚
- 每步验收必跑：ruff C901（`phase_engine` 目标值）+ `test_langgraph_parity.py` + 受影响模块测试
