# 编排助手聊天设计（Orchestrator Chat Design）

> 状态：定稿（2026-08-11）
> 前置：`docs/workflow-module-design.md`（DAG 定义 6 节点类型 + definitions API）、DAG 编辑器（`webui/frontend/src/components/workflow/WorkflowEditor.tsx`）
> 决策：在编排页左侧新增**专职聊天面板**——只做「任务拆解 → DAG 编排」，不做研究、不跑工具、不写代码执行。后端为**无状态 SSE 端点**（不复用 SessionService 会话体系），前端聊天组件自持消息状态（不绑定全局 chatStore/useSSE）。LLM 生成的 DAG spec 以**替换整个画布**方式应用（入撤销栈，可 Ctrl+Z）。

## 一、目标与边界

**做**：
- 编排页（`/dag` → 编排 Tab）左侧常驻聊天栏：输入任务 → 流式回复（拆解思路 + JSON DAG spec）→ 「应用到画布」→ 替换画布 + 自动布局 + 可撤销
- 6 节点类型限定：`llm_agent` / `planner` / `evaluator` / `approval` / `python` / `tool`
- 应用前**同构校验**（与后端保存校验一致）：类型白名单、planner/evaluator/approval 各 ≤1、无环、config 键过滤
- 面板可折叠为窄条，保护画布空间

**不做（本阶段）**：会话持久化（消息仅内存）、断点续传/SSE 重连、合并追加（只替换）、自动保存（应用后由用户点保存）、LLM 结果自修复重试、多轮引用画布编辑（current 仅作概览）。

**不改**：`SessionService`/chat 会话体系、definitions 保存校验、现有编辑器节点/保存逻辑、后端认证。

## 二、后端：`POST /api/goal/workflow/orchestrate`

无状态，JWT 自动保护（`/api/goal/workflow/*` 前缀已在 `AuthMiddleware` 保护范围）。复用 `LLMConfigBuilder`（llm.json 四层配置）+ `OpenAICompatClient.astream`（含 retry）。

### 请求体

```json
{
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "current": {
    "nodes": [{"id": "hypothesis", "label": "提出研究假设", "type": "llm_agent"}],
    "edges": [{"source": "hypothesis", "target": "data_check"}]
  }
}
```

- `messages`：最后一条必须为 `user`；≤20 条；单条 ≤8000 字符（超限 400）
- `current`：可选，仅画布概览（id/label/type），供 LLM 分析现状，**不含** config

### 系统提示词（核心约束）

1. 唯一职责：把用户任务拆解为可执行的 DAG 编排；**拒绝一切编排之外请求**（研究分析、代码编写、闲聊等）并礼貌说明
2. 节点类型限 6 类（附各类型职责说明）；`planner`/`evaluator`/`approval` 各最多 1 个；依赖无环
3. 输出结构：先简短讲拆解思路（≤3 句），随后 ` ```json ` 代码块：

```json
{
  "nodes": [
    {"id": "hypothesis", "type": "llm_agent", "label": "提出研究假设", "config": {}}
  ],
  "edges": [{"source": "hypothesis", "target": "data_check"}]
}
```

- `id`：小写英文短 id（前端会 sanitize 去重）
- `edges` 引用节点 `id`；config 可选，前端按类型白名单过滤

### SSE 事件协议

```
event: delta
data: {"text": "…增量文本…"}

event: final
data: {"text": "…完整文本…"}

event: error
data: {"detail": "llm_not_configured | invalid_request | upstream_error"}
```

- LLM 未配置（llm.json 缺失/无 key）→ 发 `error` 事件后结束，前端提示「请先在设置中配置 LLM」
- 流结束事件：连接关闭（不另发 done 事件）

## 三、前端

### 3.1 布局（WorkflowEditor 内部分栏）

```
[编排聊天 w-72 可折叠] [节点库 w-56] [画布 flex-1] [节点配置 w-72]
```

- 聊天面板**渲染在 WorkflowEditor 内部**（无 forwardRef、无 DefinitionWorkflowPage 改动），闭包直接调用编辑器内部 `applyDag`/`autoLayout`
- 折叠：头部按钮收成 w-10 窄条（仅图标），展开恢复；折叠态记忆到组件内 state

### 3.2 `OrchestratorChat.tsx`（新建）

- 自持 `messages: {role, content, streaming?}[]` + `streaming` + `error` 状态；卸载时 `AbortController.abort()`
- 发送：`fetch` POST + `ReadableStream` 手动解析 SSE（POST 无法用 EventSource），带 Bearer token
- 复用：`StreamingText`、`MarkdownRenderer`、`wf-panel`/`wf-input`/`badge-*`；输入框仿 `Composer` 视觉但自包含（Enter 发送 / Shift+Enter 换行 / 流式中可中止）
- 空态：说明文案 + 2-3 个静态示例 chip（`QuickStartChips` 绑定 session store 不可复用，自建简版）
- JSON 检测：识别 ` ```json ` 代码块 → 渲染「应用到画布」按钮
  - 应用前调 `buildNodesFromSpec` 校验；失败 → 按钮红字显示具体原因（如「含 2 个 approval 节点」），不应用
  - 成功 → toast「已应用到画布，记得保存」；无 JSON 块 → 不显示按钮，提供「复制全文」

### 3.3 纯函数（独立文件便于单测）

**`buildNodesFromSpec(spec, existingEdges)`**（新文件 `dagSpec.ts`）：
- 类型白名单（非 6 类型 → 整批失败，报出具体 id）
- `planner/evaluator/approval` 各 ≤1
- 拓扑环检测（新写 ~20 行 Kahn/DFS；前端无现成实现，后端 `core/workflow/dag.py` 不可直接复用）
- config 键过滤：仅保留该类型 `CONFIG_FIELDS`（WorkflowEditor 已有定义）内的键
- id sanitize（非法字符替换）+ 去重；缺 label → `TYPE_META` 默认 label；补 `agentColor`
- 产出 `Node[]`（`type:'dagNode'`）+ `Edge[]`（`type:'dagEdge'`）

**`parseSSE`**（新文件 `sseStream.ts`）：
- `fetch` 响应体 `ReadableStream` → 按 `\n\n` 切分事件、剥离 `event:`/`data:` 前缀
- 关键：事件跨 chunk 时留缓冲（中文多字节在 UTF-8 chunk 边界可能被切断），按行累积到完整事件再 emit

### 3.4 `client.ts`

`api.workflow.orchestrate({messages, current}, {onDelta, onFinal, onError}, signal)`：POST + 流式消费，返回 Promise。

## 四、测试计划

| 层 | 用例 |
|---|---|
| 后端 pytest | 400 校验（空 messages / 最后一条非 user / 超长）；mock astream：正常 delta+final 流、LLM 未配置 → error 事件 |
| 前端 vitest | `parseSSE`：跨 chunk 切分、`data:` 多行、空事件；`buildNodesFromSpec`：合法 spec、非法类型、双 approval、环检测、config 过滤、id sanitize/去重 |
| 前端 vitest | OrchestratorChat：发送→mock fetch SSE→流式渲染→应用按钮→applyDag 后画布节点数断言 |
| 回归 | 全量 vitest（当前 726）+ tsc + vite build |

## 五、验收标准

1. `/dag` 编排 Tab：左侧聊天栏可折叠，折叠后画布恢复宽度
2. 发「把 X 拆解为 DAG」→ 流式中文回复 → 末尾 JSON → 「应用到画布」→ 画布整体替换 + 自动布局 + Ctrl+Z 可撤销 + 保存定义成功
3. 发无关请求（写诗/聊天）→ 助手婉拒
4. 生成含 2 个 approval 的 spec → 应用按钮红字拒绝，给出原因
5. LLM 未配置 → 面板提示「请先在设置中配置 LLM」
6. dark/light 两主题样式正常（wf-* 变量）
