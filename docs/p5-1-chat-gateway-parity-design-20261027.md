# P5-1 设计 — Gateway chat 补齐（历史 / 事件 / 身份），然后删旧分支 — 2026-10-27

> ## ⚠️ 本设计已作废（2026-10-27 同日评审）
>
> 代码图审视（`code-graph-vs-architecture-gap-20261027.md`）证明本文件的前提
> 不成立：它假设「复用 MemoryManager + SessionService」是正确目标，但架构定案
> 是 **PlatformStore 是消息与事件的唯一写入者（不变量 7）**，现状的三写者
> （memory_manager / projector / web_session）恰恰是待收敛的技术债。
>
> 正确顺序：**先做 0.4 数据面收敛，再做 chat 补齐**（否则写完要推翻）。
> 保留本文件仅为记录设计推演过程，实施请以差距报告 §4 的修正路线为准。

> 前置：阶段 4 清理清单 §1.1 记录了「chat/CLI 旧分支暂缓删除」的原因。
> 目标：补齐新路径缺失的三项能力，做**执行等价**验证，然后删除旧分支与
> `new_entry_enabled` 门——所有入口最终只剩一条通道。

---

## 1. 现状差距（实测）

`api/routers/chat.py::_send_via_gateway` 当前：

| 能力 | 旧路径（SessionService） | 新路径（Gateway） |
|---|---|---|
| 读会话历史注入上下文 | ✅ `messages` 表 | ❌ 只发当前这一条 |
| 写用户/助手消息 | ✅ 写 `messages` + 投影 | ❌ 不写 |
| 事件落库（SSE 可回放） | ✅ `EventStore.emit` → projector | ⚠ 只有 Gateway 内部 store（`data_dir` 未配置时不写）；且事件无 session 绑定 |
| `message_id` | ✅ | ❌ 返回 `None` |
| 返回结构 `attempt_id` | ✅ attempt id | ✅ 用 `run_id` 顶替 |

**CLI 路径无历史缺口**（`_run_via_gateway` 已接 MemoryManager），本次只治 API chat。

## 2. 设计原则：复用现有存储，不造第二套

三者都复用**已经存在**的组件，不引入新表、新通道：

| 需求 | 复用件 | 说明 |
|---|---|---|
| 历史读写 | `core.agent.memory_manager.get_default_memory_manager()` | 与旧路径**同一个 SQLite 文件**（`SESSION_DB_FILENAME` = `web_session._get_db_path()` 的文件），因此新旧路径共享历史，切换无感 |
| 事件持久化 | `SessionService` 的 `EventStore`（**不**新建） | 事件落 `event_log` + projector 投影，SSE 立即能推——与旧路径完全同源 |
| message_id | `MemoryManager.append()` 的返回值 | 它返回消息 id（`primary_append` 返回 id；紧急缓冲返回 `emergency_*`），直接透出 |

**为什么不用 Gateway 自己的 PlatformStore**：`PlatformStore` 是**平台数据面**（per-session
事件库 + 工件登记），服务于执行面事件；WebUI 的会话/消息/SSE 走的是 `SessionService`
那条链（`event_log` + `messages` + projector）。两条链目前并存，合并属后续阶段
（0.4/E5）。P5-1 的任务是**让 chat 路径接入 WebUI 正在用的那条链**，避免维护双写。

## 3. 实现（`_send_via_gateway` 内，约 40 行）

```
① 读历史
   mm = get_default_memory_manager()
   history = await mm.get(session_id)          # 失败 → 空历史（不阻断）
② 提交，并把历史带进 payload
   payload = {"text": content, "history": history}
   receipt = gw.entry.submit(api_chat_request(session_id, content, history=history))
③ 引擎执行（现有 run_once({CHAT})）
④ 成功：写消息 + 发事件
   user_mid = await mm.append(session_id, "user", content)
   asst_mid = await mm.append(session_id, "assistant", run.answer)
   service = _get_session_service()
   service.emit_chat_completed(session_id, user_mid, asst_mid, run)  # 复用 EventStore
⑤ 返回 {"message_id": asst_mid, "attempt_id": run.run_id, "reply": ...}
```

**历史注入点**：`WorkRequest.payload["history"]`。`SessionController._execute` 目前只取
`payload["text"]` 构造单条 user 消息——扩展为「history + 当前 user 消息」。
这是**协议字段扩展**（契约白名单需同步加 `history`），且只在 payload 内，不进事件。

**失败语义**：引擎失败（`run.ok == False`）→ 消息不写、事件记 `chat_failed`，
返回 502（现状）。成功但持久化失败 → 返回 200 + `warning` 字段（用户可见的回答
不该因为存储抖动丢掉）。

## 4. 执行等价验收（replan v2 §5 方法论）

| 维度 | 旧路径 | 新路径 | 验收 |
|---|---|---|---|
| 历史条数 | 2 条/轮（user+assistant） | 同 | `messages` 表行数相等 |
| 多轮上下文 | 第二轮能看到第一轮 | 同 | 第二轮 prompt 含第一轮内容（fake client 捕获） |
| 事件 | `event_log` 有记录 | 同 | 同一 kind 序列 |
| 返回 | `message_id` 非空 | 同 | 字段存在且可在 `messages` 找到 |
| 失败 | 502/504 | 502 | 状态码与错误体一致 |

**跨路径连续性**：先用旧路径写两轮，再用新路径写一轮 → 历史共 6 条且新轮能看到
前两轮（证明同库共享，切换无感）。

## 5. 删旧分支（同一提交内，验后即删）

删除项：
- `api/routers/chat.py::send_sync` 的 `new_entry_enabled()` 分支与旧 `service.send_message` 调用；
- `cli/tui/session.py::_run_agent_loop` 的 `new_entry_enabled()` 分支（保留 Gateway 路径）；
- `entry_adapters.new_entry_enabled()` 函数 + `gateway/__init__` 导出；
- 相关测试的 off 分支（`test_entry_flags` 的 chat 门用例、`test_cli_gateway_path` 的旧路径用例）。

**保留**：`_get_session_service()` 仍被其他端点使用（会话 CRUD、SSE、study 面板），不删。

## 6. 明确不做

- PlatformStore 与 SessionService 两条存储链合并（0.4/E5，独立工程）；
- 流式 `/send`（当前就是同步接口；流式走 `/events` SSE，不受影响）；
- 多模态/附件消息（现状也不支持）；
- CLI 路径改动（已有历史，仅删门）。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| `mm.get()` 在大型历史上慢 | 现状旧路径同样读全量；先按现状对齐，优化留后续（memory 层已有 LRU/cache） |
| 历史注入改变 prompt → 回答行为变化 | 这正是等价目标（旧路径也注入历史）；用 fake client 断言 prompt 结构一致 |
| 事件双写（Gateway store + EventStore） | `_send_via_gateway` 不配置 `data_dir` 时不写 Gateway store；生产只走 EventStore 一条 |
| 删除分支后 WebUI 回归 | 先跑 `test_webui_api`/`test_webui_e2e`/`test_chat_*` 全绿再提交 |

## 8. 验收清单

1. `test_send_sync` 走新路径：历史 2 条、`message_id` 非空、事件有记录；
2. 多轮上下文可见（fake client 断言）；
3. 跨路径连续性（旧写 2 轮 → 新写 1 轮 → 6 条历史）；
4. 失败路径：502 + 不写消息；
5. `new_entry_enabled` 从代码与导出中消失（`git grep` 零命中，架构门禁测试钉死）；
6. WebUI/chat 相关套件全绿。
