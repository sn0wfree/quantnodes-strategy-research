# 阶段 4 清理清单 — 2026-10-27

> 原规划：ContextHub 4 builder→1 · 调度归一 · 反向依赖 · 测试隔离修复 ·
> CI 门禁 · 删旧路径。工量 7d。**本文件先划边界：哪些删、哪些留、哪些降级。**
> 原则：减法有据（谁在用、删了谁疼），不为了「看起来干净」而动在跑的东西。

---

## 1. 已确认可删（本轮执行）

| 项 | 依据 | 状态 |
|---|---|---|
| **旧 asyncio.Queue 路径（scheduler）** | 双门已翻默认开 + 3c 执行等价测试通过 | ✅ 已删（`_session_loop` / `_session_queues` / `_ensure_consumer`） |
| **`research_graph_enabled()` 门** | B1-b 等价性测试（RoundOutcome 逐字段相等）已钉死 | ✅ 已退役（恒 True，环境变量无效） |
| **`study_entry_enabled()` 门** | 3c 执行等价已验；旧通道已删 | ✅ 已删 |
| **`new_entry_enabled()` 门（chat/CLI 分支）** | ⚠ **暂缓**——见 §1.1 | ⏸ |

### 1.1 为什么 chat/CLI 旧路径分支暂缓删除

清理中发现的**功能缺口**：`_send_via_gateway` 目前不写对话历史、不落事件库
（`message_id: None`），而旧 `SessionService.send_message` 两条都做。直接删旧分支
= 让 WebUI 的 `/send` 失去历史持久化与事件流——属于功能退化，不是清理。

删除前置（阶段 5 第一项）：Gateway 的 chat 路径接入
1. 会话历史读写（MemoryManager / SessionService 的历史注入）；
2. `PlatformStore.emit` 事件持久化（Gateway 已有 store 参数，但 chat 路径未接）；
3. `message_id` 签发（P-4 身份）。

补齐后按 replan v2 §5 方法论做**执行等价**验证（历史条数、事件条数、返回结构），
再删旧分支。

## 2. 明确不删（保留，附理由）

| 项 | 为什么留 |
|---|---|
| **SSE 双轨（`api/sse_buffer.py` + `api/session/bridge_v2.py`）** | 3b-2 延后至今；影响面 8 个生产文件 + 7 个测试文件，且是 WebUI 实时链路的现存实现。删除属独立工程，不是「清理」 |
| **`ContextBuilder`（`core/agent/context.py`）** | 服务旧 `AgentLoop`（study/runtime 仍在用），GraphEngine 的 llm/react 节点**不用**它。合并不是删文件，是迁移调用方——属阶段 5 |
| **`core/study/langgraph_engine.py`** | B1-b 的 research preset 与它**无关**（那是独立实现）；删它必须先确认 study 的 `engine=langgraph` 无人使用。需先做使用面调查 |
| **`core/study/_archived/`** | 历史归档，零运行时成本，保留可追溯 |
| **`tests/attic/`** | 同上 |

## 3. 测试隔离修复（本轮核心，优先做）

**问题**：全量套件 order-dependent（9/5 记录在案）——合跑失败、单跑通过。这是让
「全量绿」失去警报效力的根因，也是阶段 4 最有价值的产出。

**已知三类污染源**：

| 源 | 机制 | 修法 |
|---|---|---|
| 模块级单例 | `get_gateway()` 缓存 mailbox/session 表 | 已在部分文件加 `reset_gateway()`；推广到 conftest 全局 autouse |
| 环境变量泄漏 | `QNODES_*` / `SR_*` 由测试设置未清 | conftest 全局快照/还原 `os.environ` |
| 全局注册表/缓存 | `ToolRegistry`、LLMConfig、model_catalog 等模块级 dict | 逐个加 reset fixture |

**验收**：`pytest -p no:randomly`（固定序）与 `pytest --randomly-seed=N`（随机序）
各跑 3 次，失败集合为空或稳定为「已知预存」集合。

## 4. CI 门禁

| 门 | 内容 |
|---|---|
| 分道 marker | `llm` / `e2e` / `slow` 已有，补 `pytest -m "not llm and not e2e and not slow"` 的 PR 门 |
| 依赖方向 | 一条 AST 测试：`core/{gateway,orchestration,runtimes,storage}` 不得 import `core/study`（域→平台单向） |
| 不变量 | `verify_all()` 在 CI 跑（INV-1/3/5/8/9） |
| 新门 | 无裸 `except:`、无 `print(`（`src/` 内，`noqa: T201` 白名单） |

## 5. 本轮执行顺序（保守版）

```
S1  测试隔离（conftest 全局 env 快照 + gateway 单例重置 + 注册表重置）   ✅ 完成
S2  删旧 asyncio.Queue 路径 + study 门                                  ✅ 完成
S3  删 research_graph 门（保留图路径为唯一实现）                        ✅ 完成（函数保留恒 True）
S4  依赖方向 AST 测试 + 不变量 CI + 分道门                              ✅ 完成
S5  全量分道跑 + 审计门②                                                ⏳ 待做
```

**已删除**（本轮实测）：
- `StudyScheduler._session_queues` / `_session_consumers` / `_ensure_consumer` /
  `_session_loop`（旧 asyncio.Queue 通道，约 40 行）
- `study_entry_enabled()` 门（含 3 个测试的 off 分支）
- `research_graph_enabled()` 的门接线（`_resolve_branch_fn` 恒走图；函数保留恒 True）

**新增门禁**（`tests/test_architecture_gates.py`，10 条）：
- 平台层（gateway/orchestration/runtimes/storage）不得 import `core.study`；
- `core/gateway` 零 FastAPI import；
- `verify_all()` 全绿 + INV 登记表覆盖 INV-1/3/5/8/9；
- 门退役契约（research_graph 恒 True、环境变量无效、phase_engine 无门调用）；
- kind 过滤双向不丢单。

**langgraph 分支退役（原 S?）单独列为阶段 5**，理由：它是 1000+ 行的独立执行体，
需要先调查 `study.engine='langgraph'` 的真实使用面，且删除后的回归面远超本轮。
本轮只做「门翻默认 + 旧队列/旧门删除」，不动它。

## 6. 审计门② 检查项

1. 删除兑现：清单第 1 节每项确认已删（`git grep` 零命中）；
2. 第一次真实全量绿（分道后）；
3. 原始 9/5 发现销账（安全 ×4 / plan-dag 阻塞 / 死代码簇 / 小 bug ×2）；
4. 性能基线（GraphEngine 单 turn 延迟、mailbox 吞吐）；
5. 文档对齐（架构图/目录结构/契约文档与实际代码一致）。

## 7. 阶段 5 待办（本清单明确移交）

| # | 事项 | 前置 | 理由 |
|---|---|---|---|
| P5-1 | Gateway chat 路径补历史注入 + 事件持久化 + message_id 签发，然后删 chat/CLI 旧分支 | 无 | 现新路径 `message_id: None`、不写事件库——直接删旧分支是功能退化 |
| P5-2 | 删除 `study/langgraph_engine.py` 全家 + `_run_langgraph_branch` | 先查 `study.engine='langgraph'` 真实使用面 | 约 1000+ 行 + 6 个测试文件；错误删除 = 研究执行无路可退 |
| P5-3 | SSE 单轨（删 `api/sse_buffer.py` + `api/session/bridge_v2.py`） | 3b-2 一直延后 | 8 个生产文件 + 7 个测试文件，属独立工程 |
| P5-4 | `ContextBuilder`（4 builder）并入 ContextHub | AgentLoop 迁移 | 合并不是删文件，要迁调用方 |
| P5-5 | 安全债 ×4（9/5 评估记录） | 无 | 与本次架构重构正交，需独立排查 |

## 8. 本轮实测数据

- 删除代码：约 40 行（旧队列通道）+ 门接线若干
- 新增门禁：10 条架构测试
- 回归：受影响 148 + 更宽 232 全绿；lint 全绿

## 9. 全量分道跑发现的预存问题（非本次改动引入）

| 问题 | 证据 | 性质 |
|---|---|---|
| `test_alpha_zoo_adapter` ×2 失败 | 单独跑也失败（9/21 已记录）；`compute_as_series` 返回 180 行而 wide.notna 只有 120 | **预存实现缺陷**（`compute_as_series` 未按约定 dropna） |
| `test_e2e_full_flow.py::TestNoProgress::test_no_progress_termination` 挂起 | 单跑也挂；栈显示 `_approval_event.wait(timeout=1800)`——`make_loop()` 未传 `approval_timeout`，no_progress 触发审批后无人应答，默认等 30 分钟 | **预存测试缺陷**（应传 `approval_timeout=0.1` + `approval_policy="reject"`） |

两者都不属阶段 4 的删除/隔离问题，也不由本轮改动引入。处理建议：
- alpha_zoo：修 `compute_as_series`（`wide.dropna(how="any")` 后再 stack），属独立 bugfix；
- e2e no_progress：测试补 `approval_timeout` 参数，属测试修复。
两者都列进阶段 5（P5-6/P5-7），本轮不夹带。
