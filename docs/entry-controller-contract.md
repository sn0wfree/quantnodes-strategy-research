# 统一入口 × 控制面 交互契约 — 2026-10-24

> 定位：定义 `WorkEntry`（E1）与 `SessionController`（A2）之间的**标准结构与交互协议**。
> 一句话：**入口拥有协议，控制器拥有裁决**。`place()` 是唯一写通道；同步回执只描述「收下了、放哪了」；运行的一切事实经事件流。
> 红线：WorkEntry 不得变成第二个控制面（不做准入、不排队列）；Controller 不得见到协议细节（无 HTTP/SSE/CLI/MCP 概念）。

---

## 1. 职责矩阵（一个 owner 对一件事）

| 职责 | Owner | 明确不做 |
|---|---|---|
| 契约校验 + 归一化 | WorkEntry | 不做任何 turn 裁决 |
| 幂等去重（E5） | WorkEntry | 不重放副作用 |
| `handle_id` 签发 | WorkEntry | 不签 `run_id` |
| 事件扇出 / 订阅管理 | WorkEntry（经 EventStore） | 不产生、不改写事实 |
| turn 准入 / 队列仲裁 / steer mailbox | SessionController | 不见协议字段 |
| `run_id` 签发（incarnation） | SessionController | — |
| 准入时的预算门（读预算视图） | SessionController | 不记账（账本在 ContextHub） |
| write fence（activeRunId 认领） | SessionController | — |
| 事实产生 | Controller / Runtime → EventStore | — |

入口不路由：控制面只有一个（决策 1）。若未来分片，在 `place()` 前按 `session_key` 加路由即可，契约不变。

## 2. 边界契约（标准结构，落 `core/orchestration/contracts.py`）

```python
class QueueMode(str, Enum):
    STEER = "steer"
    FOLLOWUP = "followup"
    COLLECT = "collect"      # 枚举占位；v1 入口直接拒绝（UNSUPPORTED_QUEUE_MODE）
    INTERRUPT = "interrupt"

class Disposition(str, Enum):
    """place() 同步回执——只描述「收下了、放哪了」，不承诺运行"""
    ADMITTED = "admitted"        # 已过准入屏障，run_id 已定，即将开跑
    QUEUED = "queued"            # 进等待队列（followup 语义）
    STEERED = "steered"          # 已预留到活跃 run 的注入邮箱
    DOWNGRADED = "downgraded"    # steer→followup 显式降级（必须发事件）
    REJECTED = "rejected"        # 结构性拒绝（同步，带结构化原因）

class RejectReason(str, Enum):
    # —— 入口侧拒绝：无 handle（协议错误，调用方 bug）——
    MALFORMED = "malformed"
    UNSUPPORTED_QUEUE_MODE = "unsupported_queue_mode"     # v1 的 collect
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"         # 同键异载荷
    # —— 控制器侧拒绝：有 handle + REJECTED 回执 + 事件（系统状态）——
    CAPACITY_FULL = "capacity_full"                       # 并发满（容量组）
    BUDGET_EXHAUSTED = "budget_exhausted"                 # 预算尽（准入门）
    SESSION_MUTATING = "session_mutating"                 # 变更态不可受理

@dataclass(frozen=True)
class WorkRequest:              # 对外契约（调用方构造；v5 图 ② 已定）
    kind: WorkKind              # chat / study / automation / director_propose
    source: SourceKind          # cli / api_chat(含 webui) / scheduler / mcp / director
    session_key: str
    payload: Mapping[str, Any]  # 按 kind 窄化，窄化在各 adapter 完成
    queue_mode: QueueMode
    budget: BudgetSpec | None   # 上限声明；缺省用会话默认
    idempotency_key: str | None # 副作用安全重试（E5）
    deadline: datetime | None   # 排队过期时刻；到点 → EXPIRED 事件

@dataclass(frozen=True)
class WorkItem:                 # 边界内部形态（入口归一化后交控制器）
    item_id: str                # = handle_id，入口签发
    request: WorkRequest        # 原样携带（审计与回放）
    created_at: datetime
    # 约束：不携带任何协议字段（HTTP/SSE/CLI 细节在 adapter 层剥除）

@dataclass(frozen=True)
class AdmissionReceipt:         # place() 同步回执
    item_id: str
    disposition: Disposition
    reason: RejectReason | None # REJECTED / DOWNGRADED 必填
    run_id: str | None          # ADMITTED=新 run；STEERED=目标 run；其余 None

@dataclass(frozen=True)
class RunHandle:                # 调用方持有的引用
    handle_id: str              # 入口签发，贯穿 item 生命周期
    session_key: str
    initial: AdmissionReceipt   # 提交瞬间快照；后续状态经 status()/事件流
```

**两侧接口：**

```python
class WorkEntryProtocol(Protocol):            # 对调用方（E1 交付）
    def submit(self, req: WorkRequest) -> RunHandle: ...          # 入口侧拒绝以异常/错误返回，无 handle
    def subscribe(self, session_key: str) -> AsyncIterator[Event]: ...
    def status(self, handle_id: str) -> ItemStatus: ...
    def cancel(self, handle_id: str) -> CancelResult: ...

class SessionControllerProtocol(Protocol):    # 对入口（本契约定义的边界）
    def place(self, item: WorkItem) -> AdmissionReceipt: ...      # 唯一写通道
    def status(self, handle_id: str) -> ItemStatus: ...           # 从事件 fold 的投影
    def cancel(self, handle_id: str, force: bool = False) -> CancelResult: ...
```

**关键设计规则：**
- **边界方法是快同步**（非阻塞、无 IO；mailbox 排序预留 O(1)）。异步性只出现在 `subscribe` 的事件迭代——这是 CLI（同步）与 API（asyncio）能共用一份契约的原因，直接消问题⑨里「CLI=同步 / API=asyncio.Queue」的分裂。
- **两级拒绝语义不同**：入口拒绝 = 无 handle（调用方 bug）；控制器拒绝 = 有 handle + 回执 + 事件（系统状态，可观测可重试）。
- **ItemPhase ≠ RunPhase**：`status()` 投影调用方视图（RECEIVED/QUEUED/STEERED/ADMITTED/RUNNING/SETTLED/REJECTED/EXPIRED/CANCELLED）；7 阶段状态机与 6 正交真值是控制器内部实现，只以事件形式外显，调用方不得耦合。

## 3. 交互时序

```
调用方              WorkEntry                  SessionController         EventStore
  │  submit(req)       │                            │                       │
  │───────────────────>│ 校验→幂等去重→签 handle_id  │                       │
  │                    │ place(WorkItem)───────────>│ mailbox 排序预留        │
  │                    │<──Receipt(disposition)─────│ 结构性快筛(容量/预算)    │
  │<──RunHandle────────│                            │  ┌─异步─────────────┐ │
  │                    │                            │  │ 准入屏障 → 开 run │─┼─> emit(run_started…)
  │<══════════ subscribe(session_key) ══════════════│═════════════════════│
  │                    │   （扇出；WorkEntry 不改写内容）                    │
```

四种 disposition 的完整生命周期：

| queue_mode | 同步回执 | 异步链路 |
|---|---|---|
| followup（CLI/MCP/Director） | QUEUED | 当前 turn settle → 准入屏障放行 → 签 run_id → ADMITTED → RUNNING → SETTLED |
| steer（api_chat 含 webui） | STEERED（mailbox 预留到活跃 run） | 下一个安全点：注入批次合并渲染（细则五条之 5）→ run 继续 |
| steer 且 injectable=false | DOWNGRADED（必须发事件） | 转 followup 链路 |
| interrupt（automation/cancel） | ADMITTED（取消旧 run 后优先开跑） | 旧 run 的提交被 write fence 拦截；SETTLED |
| 结构性拒绝 | REJECTED + reason | 终态，事件留痕 |

**恢复路径**（对应「事件不重放，缺口靠刷新」）：调用方丢失回执或断线 → `status(handle_id)` 从事件 fold 恢复；同幂等键重试 → 去重返回原 handle；QUEUED 项到 `deadline` → EXPIRED 事件，**QUEUED 项不表现为 executing run、不继承 run 超时**（OpenClaw 同款规则）。

## 4. 边界不变量（一致性套件的验收标准）

1. `place()` 是唯一写路径；控制器对入口只暴露 place/status/cancel 三个方法。
2. 入口拒绝无 handle；控制器拒绝必有 handle + 结构化 reason + 事件。
3. `handle_id` 只能由入口签发；`run_id` 只能由控制器签发——身份分离（B4）在边界上直接可见。
4. Receipt 不携带队列位置（第几位）等内部排序信息——调用方不得耦合内部排序。
5. DOWNGRADED / REJECTED / EXPIRED 必发事件（可观测性；「不静默」落到字段级）。
6. 进入 `place()` 的 WorkItem 不得含协议字段（结构断言：字段白名单）。
7. Director 只能构造 WorkRequest 走 `place()`——「不得自行开 run」由结构强制，不靠约定。
8. 边界方法不阻塞、无 IO（快同步），异步只在事件迭代。

## 5. 与既有决策的咬合

| 决策/刀号 | 本契约的落点 |
|---|---|
| E1 统一入口 | contracts.py 即 E1 的第一块交付；WorkEntryProtocol 全貌 |
| 决策 12（queue_mode 定稿） | Disposition 表即四种模式的运行时语义 |
| A2 SessionController | SessionControllerProtocol 是 A2 的对外形状（内部状态机不在本契约） |
| A3 write fence | interrupt 链路中「旧 run 提交被拦」由 fence 保证 |
| B3 协议版本化 | contracts 随 PROTOCOL_VERSION 走；collect 的启用 = 版本能力协商 |
| B4 身份分离 | handle_id / run_id 双签发是第一个落点 |
| E5 幂等键 | 入口去重 + IDEMPOTENCY_CONFLICT |
| 问题⑨（8 种启动方式） | 快同步边界 = CLI 与 asyncio 共用契约的结构基础 |

## 6. 开放点（2026-10-24 已全部拍板）

1. **session 惰性创建** ✅——首 item 自动物化 session，不要求预建。
2. **MCP 来源默认 queue_mode** ✅——followup（对外工具协议，保守排队）。
3. **deadline 进 E1 首版** ✅——字段随契约一次定形（OpenClaw 持久化只存 deadline 的模式）。
4. **status() 折叠来源** ✅——从 EventStore fold（与统一快照同源），控制器不养读模型。

## 7. E1 一致性测试清单（本边界的验收）

1. 五种 disposition 全覆盖（仲裁器 stub）；
2. 两级拒绝的 handle 有无断言；
3. 同幂等键同载荷 → 同 handle；异载荷 → IDEMPOTENCY_CONFLICT；
4. collect（v1）→ 入口 UNSUPPORTED_QUEUE_MODE，无 handle；
5. WorkItem 协议字段白名单断言（不变量 6）；
6. DOWNGRADED/REJECTED/EXPIRED 事件必发断言（不变量 5）；
7. QUEUED 项不出现在 executing 列表、不继承 run 超时；
8. 边界方法快同步断言（不 await、不 IO）。
