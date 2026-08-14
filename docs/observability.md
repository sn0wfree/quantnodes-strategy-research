# 可观测性（Observability）

策略研究平台的可观测性 = **三层信号**：运行时状态（dump）、写入吞吐（metrics）、
卡死防护事件（hangs）。本文档是运维入口：先看"现在卡没卡"，再看"过去 24h 卡了几次"。

## 总览

| 能力 | 入口 | 鉴权 | 何时用 |
|---|---|---|---|
| 进程内状态 dump | `GET /api/study/_internal/dump?session_id=` | X-Admin-Token | 卡死 triage：哪个 study 在跑、跑几轮、心跳多老 |
| 会话写入吞吐 | `GET /api/admin/metrics` | X-Admin-Token | 磁盘/DB 写入是否异常（rate / duration / success） |
| 卡死防护事件报告 | `GET /api/admin/hangs/report` | X-Admin-Token | 过去 24h 各防护层触发频率，阈值调优依据 |
| 卡死防护事件（CLI） | `strategy-research hangs --hours 24` | 本地 | 无 HTTP 时手动补跑日报 |

Admin token：`SR_ADMIN_TOKEN` 环境变量。未设置时 admin 端点返回 `503`。

---

## 1. 进程内状态 dump（A）

**端点**：`GET /api/study/_internal/dump?session_id=<sess>&study_id=<可选>`
（`X-Admin-Token` 头）

返回当前进程（而非 DB 回放）的调度器 + 全部该 session 的 study 状态：

```json
{
  "session_id": "sess-x",
  "db_path": "/home/ll/.quantnodes-research/goals.db",
  "generated_at": "2026-08-14T13:00:00+00:00",
  "watchdog":   { "alive": true, "interval_s": 60, "heartbeat_timeout_s": 3600 },
  "concurrency":{
    "semaphore_limit": 3,
    "queued_study_ids": [], "active_executor_ids": ["st-1"], "active_task_ids": ["st-1"],
    "queued_count": 0, "active_count": 1
  },
  "session_queues": { "sess-x": { "queued_depth": 0, "consumer_alive": true } },
  "studies": [{
    "study_id": "st-1", "objective": "...", "strategy_name": "...",
    "execution_status": "running", "current_round": 3, "max_rounds": 20,
    "heartbeat": "2026-08-14T12:59:30+00:00", "heartbeat_age_s": 30.2,
    "last_error": null,
    "hanging_protection": {
      "is_active_in_scheduler": true,
      "heartbeat_stale": false,
      "watchdog_will_interrupt": false
    }
  }],
  "hanging_signals_in_window": {
    "wallclock_timeout": 0, "log_stall": 0, "no_progress": 0,
    "circuit_breaker_open": 0, "watchdog_interrupt": 0
  }
}
```

**triage 决策表**（卡死排查）：

| 现象 | 判定 | 处置 |
|---|---|---|
| `watchdog.alive=false` | 调度器 watchdog 没起来 | 检查进程启动日志；基本不会发生（lazy 拉起） |
| `watchdog_will_interrupt=true` | 该 study 会在下次 sweep 被强制中断 | 若不想等，手动 cancel；若想救，确认 LLM/回测是否真活着 |
| `heartbeat_age_s` 持续增长 | 轮间无进展 | 查 `study_rounds` 最近完成时间；回测子进程是否 `log_stall` |
| `hanging_signals_in_window.no_progress>0` | agent 连续 N 次相同 tool_call | 查该 study 最近轮 agent 日志，考虑给 agent 换工具/换目标 |
| `concurrency.active_count=0` 但 status=running | 状态与调度器脱钩（DB 残留） | `submit()` 幂等会拒绝；直接 cancel 后重新 start |

`/api/study/_internal/dump` 不检查 session 归属（operator 级），但挂在
`/api/study/_internal/` 前缀下，`AuthMiddleware` 已放行（admin 鉴权）。

## 2. 会话写入吞吐（B）

**端点**：`GET /api/admin/metrics?recent=20`（X-Admin-Token）

包装 `core/session/metrics.py::MetricsLogger`（内存环形 10k + JSONL 落盘）。
返回：

```json
{
  "status": "ok",
  "stats": {
    "total_writes": 4821, "total_messages": 90213,
    "success_rate": 0.998, "avg_rate": 312.4, "max_rate": 2090.1,
    "min_rate": 0.0, "avg_duration": 0.004, "total_duration": 19.1
  },
  "recent": [ { "ts": ..., "count": ..., "duration": ..., "rate": ..., "ok": true } ]
}
```

解读：
- `success_rate` 明显低于 1 → 磁盘写失败（锁/磁盘满），配合 `recent[].ok=false`
- `avg_duration` 漂移 → DB 膨胀/锁竞争；对照 `strategy-research session stats` CLI 双检

**CLI 双胞胎**：`strategy-research session stats`（内存 + JSONL 聚合）。

## 3. 卡死防护事件报告（C）

**事件表**：`goals.db::hanging_events`（5 类事件，`core/study/hanging_events.py`）

| event_type | 触发点 | 阈值 |
|---|---|---|
| `wallclock_timeout` | LLM 流式墙钟超时 | `SR_AGENT_WALLCLOCK_TIMEOUT`（默认 1800s） |
| `log_stall` | 回测/后台子进程日志停滞 | `SR_BACKTEST_STALL_TIMEOUT`（默认 300s） |
| `no_progress` | agent 连续 N 次相同 tool_call | `no_progress_window`（默认 3） |
| `circuit_breaker_open` | 工具熔断器 OPEN | `failure_threshold` / `max_total_failures` |
| `watchdog_interrupt` | scheduler 心跳陈旧强制中断 | `SR_STUDY_HEARTBEAT_TIMEOUT`（默认 3600s） |

**HTTP**：`GET /api/admin/hangs/report?hours=24&limit=50` → `{status, window_hours, report}`，
`report` 含 `total_events / by_type / by_study / recent`。

**CLI**：`strategy-research hangs --hours 24 --limit 50`（本地直连 goals.db）。

**用法**：阈值调优看 `by_type` 分布——`wallclock_timeout` 频繁 → 墙钟太短；
`no_progress` 高 → agent 卡循环而非超时；`watchdog_interrupt` 高 → 心跳间隔/轮时长
不匹配。这是每天（或每批研究后）跑的"卡死日报"。

---

## 4. 结构化日志 + trace_id（D）

### 4.1 启用 JSON 日志

```
SR_LOG_JSON=1   # 启用单行 JSON 日志（默认关闭，输出纯文本）
```

启用后每行日志变为：

```json
{"ts":"13:45:01","level":"INFO","logger":"strategy_research.core.llm.openai_client",
 "trace_id":"a1b2c3d4e5f6","session_id":"sess-1","study_id":"st-9","round_num":3,
 "msg":"stream retryable status 429 (attempt 1/3)"}
```

`trace_id`/`session_id`/`study_id`/`round_num` 为空时自动省略，减少噪声。

### 4.2 trace_id 传播链

四个 `ContextVar` 在 asyncio task 图中自动传播（`asyncio.create_task`
拷贝当前 context）：

```
HTTP /api/chat/send_async
  └─ SessionService._run_attempt          ← bind: trace_id=attempt_id, session_id
       └─ AgentLoop._run_loop_core        ← fallback bind: session_id (if not set)
            └─ client.astream / achat     ← 日志含 trace_id + session_id
            └─ StudyRunner._run_one_round ← bind: study_id, round_num
                 └─ client.stream / chat  ← 日志含 study_id + round_num
```

**绑定点**（代码位置）：

| 层 | 文件:函数 | 绑定的字段 |
|---|---|---|
| 会话请求 | `api/session/service.py::_run_attempt` | `trace_id`(=attempt_id), `session_id` |
| Agent 循环 | `core/agent/loop.py::_run_loop_core` | `trace_id`(fallback), `session_id`(fallback) |
| Study 轮次 | `core/study/runner.py::_run_one_round` | `study_id`, `round_num` |

`_run_loop_core` 仅在 ContextVar 未被上层设置时才 fallback（`if not _trace_id.get()`），
所以 study runner 内的 agent 调用会继承 `_run_attempt` 设的 trace_id +
`_run_one_round` 设的 study_id/round_num。

### 4.3 grep / jq 示例

```bash
# 所有 LLM 墙钟超时事件（JSON 日志）
journalctl -u strategy-research | jq 'select(.msg | contains("wall-clock"))'

# 某次 attempt 的完整调用链
journalctl -u strategy-research | jq 'select(.trace_id=="a1b2c3d4e5f6")'

# 某 study 的所有轮次日志
journalctl -u strategy-research | jq 'select(.study_id=="st-9")'
```

### 4.4 TraceFilter + JsonFormatter 内部

- `core/observability/trace.py` -- 全部实现（4 个 ContextVar + `bind_trace`
  context manager + `TraceFilter` + `JsonFormatter` + `setup_trace_logging`）
- `TraceFilter` 挂在 root logger 上，每条 LogRecord 经过时注入
  `record.trace_id/session_id/study_id/round_num`
- `setup_trace_logging()` 幂等（filter 只挂一次），`SR_LOG_JSON=1` 时
  把所有 root handler 的 formatter 换成 `JsonFormatter`

### 4.5 已修 bug

`_check_wallclock` 之前读 `getattr(self.config, "session_id", None)`，
但 `LLMConfig` 无 `session_id` 字段 -> 恒 `None`。改为从 ContextVar
`_session_id.get()` 读取，wallclock 超时事件现在能正确关联到 session。

---

## 5. 埋点与失败容忍

- 所有事件写入 `best-effort`：写失败吞异常，**不影响主路径**（LLM 流、agent 循环、
  调度器）。
- `hanging_events` 与 `studies` 同文件（goals.db），`CREATE TABLE IF NOT EXISTS`
  幂等，无迁移。
- 事件表会无限增长（每事件一行）。建议定期 `DELETE FROM hanging_events
  WHERE created_at < strftime('%s','now')-30*24*3600`（30 天），或跑
  `strategy-research hangs` 后手工清。

## 6. 页面可观测性（Phase 3/4）

### 6.1 轮次详情端点（Phase 3）

| 端点 | 说明 |
|---|---|
| `GET /api/study/{id}/rounds/{n}/artifacts` | 轮次产物文件列表（round 根 + run_* 目录，path/size/mtime 按时间倒序） |
| `GET /api/study/{id}/rounds/{n}/manifest` | manifest.json（hypothesis / strategy_changes / metrics / verdict / next） |
| `GET /api/study/{id}/rounds/{n}/diff?against={m}` | 两轮 strategy.py unified diff；`against=0` 对 baseline；返回 add/del/context 行 + 统计 |
| `POST /api/study/{id}/rounds/{n}/adopt` | 非破坏性采用：复制该轮 strategy.py 到 `ws/study/{id}/baseline/`（下一轮继承源），不动共享 `strategies/<name>/baseline` |

### 6.2 事件与 trace 接 UI（Phase 4）

- `GET /api/study/{id}/hanging_events?hours=24&limit=20`：该 study 近 N 小时
  卡死事件（`by_type` 计数 + `recent` 列表，含 `created_at_iso`）。
- 所有 `study_*` SSE 事件（`study_started` / `study_round` / `study_failed` …）
  均带 `trace_id` / `study_id` / `round_num`。`trace_id` 每 study 一次生成、
  跨轮稳定（`AutoresearchRunner._trace_id`），页面错误条可一键复制去日志查询：
  `journalctl -u strategy-research | jq 'select(.trace_id=="...")'`。

### 6.3 状态机 v2（Phase 5）

- `GET /api/study/{id}/available_actions`：返回当前状态允许的操作
  （name / label / destructive）—— **前端按钮由后端驱动**，不再硬编码。
- `POST /api/study/{id}/actions/{name}`：统一操作入口
  （pause / resume / resume_interrupted / cancel，body 可带 `reason`）；
  状态不允许时返回 409。
- `POST /api/study/{id}/rounds/{n}/redo`：丢弃该轮（DB 行 + state.json
  回退 + round 目录删除）并重新排队从 `round n-1` 重跑；running 状态拒绝（409）。
- 操作矩阵定义在 `core/study/models.py::ACTION_MATRIX`：QUEUED→{cancel}，
  RUNNING→{pause,cancel}，PAUSED→{resume,cancel}，INTERRUPTED→{resume_interrupted}，
  MONITORING→{pause,cancel}；终态（complete/cancelled/error/budget_limited/
  early_stopped/needs_refresh）无操作。

## 7. 相关代码

| 组件 | 文件 |
|---|---|
| dump 端点 + 组装 | `src/strategy_research/api/routers/study.py`（`_study_dump`） |
| scheduler dump helper | `src/strategy_research/core/study/scheduler.py` |
| admin metrics | `src/strategy_research/api/routers/admin.py` |
| 事件表 + 报告 | `src/strategy_research/core/study/hanging_events.py` |
| 事件埋点 | `core/llm/openai_client.py`、`core/study/scheduler.py`、`core/agent/loop.py`、`core/agent/circuit_breaker.py` |
| trace context + JSON | `core/observability/trace.py` |
| trace 绑定点 | `api/session/service.py`、`core/agent/loop.py`、`core/study/runner.py` |
| 轮次详情端点 | `api/routers/study.py`（artifacts/manifest/diff/adopt/redo/hanging_events/actions） |
| 状态机矩阵 | `core/study/models.py::ACTION_MATRIX` + `allowed_actions()` |
| 测试 | `tests/test_study_dump.py`、`tests/test_admin_metrics.py`、`tests/test_hanging_events.py`、`tests/test_trace_context.py`、`tests/test_study_round_detail.py`、`tests/test_study_actions.py` |
