# 待办 — 删除旧 chat 分支（标记，未执行）

> 状态：**已标记，延后执行**（2026-10-27）。前置条件已全部就位，可随时开工。

---

## 1. 为什么可以删了

P5-1 + P5-1b 已把新路径补齐到与旧路径等价：

| 能力 | 状态 |
|---|---|
| 历史注入 | ✅ `SessionStore.get_messages` + `_convert_messages_to_history` |
| 消息持久化 | ✅ `event_bus.emit` → projector |
| `message_id` | ✅ |
| 流式输出 | ✅ llm/react 节点 `stream=True` |
| 工具链 | ✅ Gateway 装配 `build_default_registry()` |
| 推理不泄漏 | ✅ MiniMax `<think>` 跨 chunk 缓冲（`3d03ed65`） |
| 真实 LLM 验证 | ✅ 3 项（流式 / 工具 491 / 历史 742） |

## 2. 要删的东西

**入口分支**（2 处）：

| 文件 | 行 | 内容 |
|---|---|---|
| `api/routers/chat.py` | 875-876 | `new_entry_enabled()` 分支（`/send` 的旧路径） |
| `cli/tui/session.py` | 236-238 | CLI 的 `new_entry_enabled()` 分支 |

**门本身**：

- `core/gateway/entry_adapters.py:22` `new_entry_enabled()` 函数；
- `core/gateway/__init__.py` 的导入与 `__all__` 条目。

**旧路径代码**（删除后成为死代码）：

- `api/session/service.py::SessionService.send_message` 及其队列（`_process_session_queue` / `_run_attempt`）；
- `api/routers/chat.py::_run_agent_loop_background` 及 `_persist_user_message` / `_persist_assistant_message`；
- 相关测试的旧路径用例。

## 3. 删除前必须确认的

| # | 检查项 | 说明 |
|---|---|---|
| 1 | `/send_async` 端点 | 它独立于 `/send`，走 SessionService 的队列。删除 SessionService 会连带影响——**需单独评估是否也在本次迁移范围** |
| 2 | `loop_stream` / SSE 流式管线 | 旧路径的逐 token 推送用了 `_PartAccumulator`，新路径用 `install_sink`。确认前端消费的事件名一致 |
| 3 | study / goal / hypothesis 是否依赖 SessionService | 这些域可能通过 session 队列发消息 |
| 4 | CLI 的 `_run_via_gateway` | 已存在且可用（P5-1 前就接了 MemoryManager 历史），删门后成为唯一路径 |
| 5 | 全量回归 | 删除后跑完整套件（非 live + live） |

## 4. 建议的删除顺序

```
① 先删 CLI 分支（影响面小，CLI 的 Gateway 路径已验证）
② 再删 API /send 分支
③ 观察一轮（跑测试 + 手动验证 WebUI）
④ 删 new_entry_enabled 函数与导出
⑤ 清理死代码（SessionService 等，需先做 §3 检查）
```

**不要一次性全删**——入口分支和 SessionService 是两个独立层次，分开可回退。

## 5. 恢复方式

若删除后发现问题：`git revert <commit>`。因为删除是独立提交，回退干净。

---

## 6. 相关文档

- `p5-1-chat-gateway-parity-design-20261027.md`（历史/持久化设计）
- `p5-1b-streaming-tools-design-20261027.md`（流式/工具链设计）
- `stage-4-cleanup-checklist-20261027.md` §7（阶段 5 待办清单）
