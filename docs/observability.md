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

## 4. 埋点与失败容忍

- 所有事件写入 `best-effort`：写失败吞异常，**不影响主路径**（LLM 流、agent 循环、
  调度器）。
- `hanging_events` 与 `studies` 同文件（goals.db），`CREATE TABLE IF NOT EXISTS`
  幂等，无迁移。
- 事件表会无限增长（每事件一行）。建议定期 `DELETE FROM hanging_events
  WHERE created_at < strftime('%s','now')-30*24*3600`（30 天），或跑
  `strategy-research hangs` 后手工清。

## 5. 相关代码

| 组件 | 文件 |
|---|---|
| dump 端点 + 组装 | `src/strategy_research/api/routers/study.py`（`_study_dump`） |
| scheduler dump helper | `src/strategy_research/core/study/scheduler.py` |
| admin metrics | `src/strategy_research/api/routers/admin.py` |
| 事件表 + 报告 | `src/strategy_research/core/study/hanging_events.py` |
| 事件埋点 | `core/llm/openai_client.py`、`core/study/scheduler.py`、`core/agent/loop.py`、`core/agent/circuit_breaker.py` |
| 测试 | `tests/test_study_dump.py`、`tests/test_admin_metrics.py`、`tests/test_hanging_events.py` |
