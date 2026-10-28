# A1 测试分道与基线修复 — 计划 + 执行记录 — 2026-10-28

> 依据：`project-status-and-roadmap-20261028.md` §4 实测基线 +
> 本轮三方 commit 对比（`33548a2a` P5-2 前 / `616085ee` 安全前 / 当前）。

## 0. 执行结果（2026-10-28 已完成）

| 步骤 | 结果 |
|---|---|
| 步骤 1 分道补全 | ✅ 5 个 e2e 套件入 `_LANE_E2E_FILES`；PR 道收集 9862 → **9814**；`timeout = 180` 全局兜底 |
| 步骤 2 我方回归 | ✅ `TestRebuildPhaseOutputs` 死测试删除（`ea2f3563`） |
| 步骤 3a posix skip | ✅ `test_shell_tool` 6 失败 → 9 skipped（四类 POSIX 命令套件） |
| 步骤 3b compact patch | ✅ 13 失败 → **0**（并修出一个真产品 bug，见 §6） |
| 步骤 3c persister | ✅ `test_route_agent_event_*` 11 失败 → **0**（同源于 §6 的导入绑定 bug） |

**四族合计：30 个失败 → 0**（提交 `e75cc4c7`），验证 50 passed / 9 skipped。

## 6. 执行中发现的真产品 bug（重要）

`loop_compaction.py` 有两处 **from-import 绑定陷阱**：

```python
from .loop import _compaction_persister   # 绑定 None（模块加载时的值）
from .compact import compact_messages     # 测试 patch 不到真实调用点
```

后果：`register_compaction_persister()` 之后注册的回调**永远不会生效**，
压缩持久化路径在任何「注册晚于 import」的部署里都会抛
`compaction_persister not registered`——生产路径同样中招，不只是测试。

修法：改为模块引用（`_loop_mod._compaction_persister` /
`_compact_mod.compact_messages`），调用时动态取值。

**这是本轮最有价值的产出**：13 个测试失败背后藏着一个真实的运行时缺陷。

## 1. 调查结论（推翻此前的「127 个全部预存」）

**归因必须用 P5-2 之前的 commit 做基线**——此前的对比基线 `616085ee` 已在
P5-2 之后，导致 P5-2 引入的回归被误判为预存。

三方对比（5 个失败最集中文件）：

| 基线 | 失败数 | 说明 |
|---|---|---|
| `33548a2a`（P5-2 前） | 11 | 真预存 |
| `3973a8bf`（当前） | 25 | **+14 由我们的改动引入** |

**我引入的（必须修）**：`test_runner_dag_helpers.py::TestRebuildPhaseOutputs`
13 条 + `TestDecisionObject` 1 条——P5-2 删除 `_rebuild_phase_outputs` 时，
我只 grep 了 import 级引用，漏了按方法名调用的测试类。**已修**（删 131 行死测试，
文件 12 passed）。

**真预存（A1 要处理）**：

| 簇 | 数量 | 根因 | 处置 |
|---|---|---|---|
| `test_shell_tool` | 6 | POSIX 命令（pwd/ls/python -c）在 Windows 无法运行 | 加 `_posix_only` skip（照抄 `test_auth_tokens_hardening` 既有模式） |
| `test_compact_*` | 13 | patch 不存在的符号 `loop.compact_messages`（实际在 `compact.py`） | 改 patch 目标 |
| `test_route_agent_event_*` | 11 | `compaction_persister not registered`——测试缺注册 | fixture 注册 stub persister |
| `test_study_runner_integration` | 7 | 待逐个定性 | 调查后定 |
| 其余分散 | ~70 | 待逐个定性 | 调查后定 |

## 2. 分道缺陷（根因，优先修）

**5 个无限阻塞套件没进 `_LANE_E2E_FILES` 白名单**：

```
test_e2e_full_flow.py          ← approval wait 1800s
test_e2e_chat.py               ← live uvicorn
test_e2e_comprehensive.py      ← live uvicorn
test_webui_e2e.py              ← live uvicorn
test_webui_e2e_playwright.py   ← browser
```

后果：PR 道会跑它们并无限挂起 → 任何全量验证都跑不完。这是 A1 的第一优先。

## 3. 执行步骤

### 步骤 1：分道补全（0.5h，最高优先）

1. 上表 5 个文件加入 `_LANE_E2E_FILES`；
2. `pyproject.toml` 增加全局兜底超时（`timeout = 120`，pytest-timeout 已装）；
3. **验收**：PR 道能在 10 分钟内跑完并给出完整结果（不再挂起）。

### 步骤 2：修我引入的回归（已完成，0.2h）

`TestRebuildPhaseOutputs` 死测试已删除。**验收**：该文件 12 passed。

### 步骤 3：真预存簇修复（按簇推进，每簇独立提交）

| # | 簇 | 修法 | 工量 |
|---|---|---|---|
| 3a | `test_shell_tool` ×6 | 加 `_posix_only` skip（Windows 无 POSIX shell） | 0.2h |
| 3b | `test_compact_*` ×13 | patch 目标 `loop.compact_messages` → `compact.compact_messages` | 0.5h |
| 3c | `test_route_agent_event_*` ×11 | fixture 里 `register_compaction_persister(stub)` | 0.5h |
| 3d | 其余 ~70 | 逐个定性后分类：环境性 skip / 陈旧测试删 / 真 bug 修 | 4-6h |

### 步骤 4：验收基线

1. PR 道（`-m "not llm and not e2e and not slow"`）固定序跑 3 次，失败集合稳定；
2. 随机序（`--randomly-seed=N`）跑 3 次，失败集合稳定；
3. 把稳定基线数字写回 `project-status-and-roadmap-20261028.md`。

## 4. 不做什么

- **不修旧执行体的失败**（`test_route_agent_event_*` 之外那些与 AgentLoop 深度
  耦合的）：C1 迁移会删掉它们，现在修是白做。判定标准——失败是否只在旧
  `AgentLoop`/`runner` 路径上。
- **不追求 127 个全绿**：目标是「失败集合稳定可解释」，而不是「清零」。
  其中一部分是 Windows 环境性（POSIX 命令、文件锁），加 skip 是正解。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 加 skip 掩盖真 bug | 每个 skip 必须写明「为什么在此环境不可能通过」 |
| 改 patch 目标改变测试语义 | 对照被测函数的真实导入点改，跑通后人工核对断言仍成立 |
| 分道后 PR 道覆盖下降 | 白名单只加「会挂起」的套件，不是「慢」的套件 |
