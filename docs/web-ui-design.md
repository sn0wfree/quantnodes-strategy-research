# Web UI 设计文档

> Strategy Research — 前端重设计方案
> 版本: v0.1 | 日期: 2026-07-28

---

## 1. 概述

### 1.1 目标

将现有 Jinja2 + HTMX 的 Web UI 重写为 React SPA，实现：
- 聊天界面（流式渲染、工具调用展示、多轮对话）
- 多 Agent 协作展示（活动时间线 + 状态卡片）
- Goal Workflow 可视化（DAG 图 + 实时状态 + 启动/暂停）
- 用户认证 + 会话持久化
- 深色主题 + 中文界面

### 1.2 设计参考

| 项目 | 借鉴点 |
|---|---|
| **OpenCode** | Message + Parts 模型、SSE 流式、Plan/Build 双模式、子会话导航、命令面板 |
| **Nanobot** | Activity Timeline（折叠活动组）、ReasoningRow（推理展示）、React SPA 架构 |
| **Codex CLI** | MarkdownStreamCollector（流式渲染）、ExecCell（工具调用）、Agent Picker（状态点） |
| **当前 TUI** | StreamingText（流式打字机）、ToolCall 行内展示、GoalPanel、WorkflowEventBus |

### 1.3 技术选型

| 层 | 技术 | 理由 |
|---|---|---|
| **框架** | React 18 + TypeScript | 生态最成熟，Radix UI 组件库支持好 |
| **构建** | Vite 5 | 快速 HMR，原生 ESM |
| **样式** | Tailwind CSS 4 | 实用优先，深色主题开箱即用 |
| **UI 组件** | Radix UI | 无障碍、可定制、无样式 opinions |
| **图标** | Lucide React | 轻量、一致的图标库 |
| **状态管理** | Zustand | 比 Redux 轻量，比 Context 强大 |
| **Markdown** | react-markdown + remark-gfm + react-syntax-highlighter | 成熟的 Markdown 渲染链 |
| **Diff** | react-diff-viewer-continued | 语法高亮 diff 展示 |
| **DAG** | @xyflow/react (React Flow) | 专业流程图/DAG 可视化库 |
| **实时通信** | SSE (EventSource API) | 浏览器原生支持，无需 WebSocket |
| **认证** | JWT (python-jose + bcrypt) | 无状态、易集成 |
| **数据库** | SQLite (已有 SessionDB) | 复用现有基础设施 |

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        React 前端                               │
│                   (webui/frontend/)                              │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 聊天模块  │  │ Agent    │  │ Workflow │  │ Goal     │       │
│  │          │  │ 面板模块  │  │ DAG 模块 │  │ 面板模块  │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       │             │             │             │               │
│       └─────────────┴─────────────┴─────────────┘               │
│                           │                                     │
│                    Zustand Store                                │
│                           │                                     │
│                    API Client + SSE                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────┴─────────────────────────────────────┐
│                       FastAPI 后端                               │
│                                                                 │
│  /api/auth/*    ─ 认证 (JWT)                                    │
│  /api/chat/*    ─ 聊天 (SSE 流式)                               │
│  /api/session/* ─ 会话管理                                      │
│  /api/goal/*    ─ Goal + Workflow                               │
│  /api/agent/*   ─ Agent 状态                                    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                        数据层                                    │
│                                                                 │
│  users.db     ─ 用户 + Web 会话 + 消息 (新增)                    │
│  sessions.db  ─ CLI 会话 (已有，保留)                             │
│  goals.db     ─ Goal + Evidence (已有，保留)                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 前端目录结构

```
webui/frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── index.html
└── src/
    ├── main.tsx                          # 入口
    ├── App.tsx                           # 根组件 + 路由
    ├── api/                              # API 客户端层
    │   ├── client.ts                     # fetch wrapper (JWT 自动注入)
    │   ├── sse.ts                        # SSE 连接管理器
    │   └── types.ts                      # TypeScript 类型定义
    ├── stores/                           # Zustand 状态管理
    │   ├── auth.ts                       # 认证状态 (user, token, login/logout)
    │   ├── session.ts                    # 会话状态 (current_session, sessions_list)
    │   ├── chat.ts                       # 聊天状态 (messages, streaming, thinking)
    │   ├── agents.ts                     # Agent 状态 (agent_list, agent_status)
    │   ├── workflow.ts                   # Workflow 状态 (dag, execution, progress)
    │   └── ui.ts                         # UI 状态 (sidebar, right_panel, theme)
    ├── components/
    │   ├── layout/                       # 布局骨架
    │   │   ├── AppShell.tsx              # 三栏布局容器
    │   │   ├── TopBar.tsx                # 顶部导航栏
    │   │   ├── Sidebar.tsx               # 左侧边栏 (会话/工作流列表)
    │   │   ├── RightPanel.tsx            # 右侧面板 (Agent/Goal)
    │   │   └── StatusBar.tsx             # 底部状态栏
    │   ├── auth/                         # 认证页面
    │   │   ├── LoginPage.tsx             # 登录页
    │   │   ├── RegisterPage.tsx          # 注册页
    │   │   └── AuthGuard.tsx             # 路由守卫
    │   ├── chat/                         # 聊天模块
    │   │   ├── MessageList.tsx           # 消息列表 (虚拟滚动)
    │   │   ├── MessageBubble.tsx         # 用户消息气泡
    │   │   ├── AssistantMessage.tsx      # 助手消息 (Markdown)
    │   │   ├── StreamingText.tsx         # 流式文本 (打字机效果)
    │   │   ├── ThinkingBlock.tsx         # 推理过程 (可折叠)
    │   │   ├── ToolCallBlock.tsx         # 工具调用 (⏳→✔/✘)
    │   │   ├── DiffBlock.tsx             # 文件 diff 展示
    │   │   ├── ImageBlock.tsx            # 图片展示
    │   │   ├── Composer.tsx              # 输入框 (@mention + 图片粘贴)
    │   │   └── MarkdownRenderer.tsx      # Markdown 渲染器
    │   ├── agents/                       # Agent 展示模块
    │   │   ├── AgentCard.tsx             # Agent 状态卡片 (右侧面板)
    │   │   ├── AgentTimeline.tsx         # Agent 活动时间线 (主聊天区)
    │   │   ├── ActivityCluster.tsx       # 折叠活动组
    │   │   └── AgentPicker.tsx           # Agent 切换器 (顶部栏)
    │   ├── workflow/                     # Workflow 模块
    │   │   ├── WorkflowDAG.tsx           # React Flow DAG 可视化
    │   │   ├── DAGNode.tsx               # 自定义 DAG 节点
    │   │   ├── DAGEdge.tsx               # 自定义 DAG 边
    │   │   ├── WorkflowList.tsx          # Preset 列表 (卡片式)
    │   │   ├── WorkflowStart.tsx         # 启动表单
    │   │   └── WorkflowProgress.tsx      # 进度条 + 事件日志
    │   ├── goal/                         # Goal 模块
    │   │   ├── GoalPanel.tsx             # 目标进度面板
    │   │   ├── CriteriaList.tsx          # 标准清单
    │   │   └── EvidenceTimeline.tsx      # Evidence 时间线
    │   └── common/                       # 通用组件
    │       ├── Badge.tsx                 # 状态徽章
    │       ├── Spinner.tsx               # 加载动画
    │       ├── EmptyState.tsx            # 空状态
    │       └── ConfirmDialog.tsx         # 确认弹窗
    └── styles/
        └── globals.css                   # Tailwind 入口 + 全局样式
```

---

## 3. 页面布局设计

### 3.1 整体布局

```
┌──────────────────────────────────────────────────────────────────┐
│  🟢 Strategy Research   [💬 聊天] [🎯 目标] [📊 工作流]   🔔 👤 │  ← TopBar (h:48px)
├─────────┬────────────────────────────────────┬───────────────────┤
│         │                                    │                   │
│ 侧边栏   │           主面板                    │    右侧面板       │
│ (260px)  │          (flex-1)                 │    (320px)        │
│         │                                    │                   │
│ ┌─────┐ │  ┌──────────────────────────────┐ │  ┌─────────────┐ │
│ │会话  │ │  │                              │ │  │ 📋 AGENTS   │ │
│ │列表  │ │  │     消息列表 / DAG / Goal     │ │  │ ┌─────────┐│ │
│ │     │ │  │                              │ │  │ │ 🤖 agent ││ │
│ ├─────┤ │  │                              │ │  │ │ ● status ││ │
│ │工作流│ │  │                              │ │  │ └─────────┘│ │
│ │列表  │ │  │                              │ │  │ ┌─────────┐│ │
│ │     │ │  ├──────────────────────────────┤ │  │ │ 🤖 agent ││ │
│ └─────┘ │  │ > 输入消息...          📎 📷 ▸│ │  │ │ ● status ││ │
│         │  └──────────────────────────────┘ │  │ └─────────┘│ │
│         │                                    │  ├─────────────┤│
│         │                                    │  │ 🎯 GOAL    ││
│         │                                    │  │ 进度 + 清单  ││
│         │                                    │  └─────────────┘│
├─────────┴────────────────────────────────────┴───────────────────┤
│  F1 帮助 │ Ctrl+1 侧栏 │ Ctrl+2 面板 │ Ctrl+G 暂停 │ UTF-8    │  ← StatusBar (h:24px)
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 响应式断点

| 断点 | 布局 |
|---|---|
| ≥1280px | 三栏（侧边栏 + 主面板 + 右侧面板） |
| 768-1279px | 两栏（主面板 + 右侧面板可折叠） |
| <768px | 单栏（侧边栏/右侧面板为抽屉） |

### 3.3 主题色板（深色）

```css
/* 背景 */
--bg-primary:    #0d1117;  /* 最深背景 */
--bg-secondary:  #161b22;  /* 卡片/面板背景 */
--bg-tertiary:   #21262d;  /* 输入框/hover */
--bg-elevated:   #30363d;  /* 弹窗/下拉 */

/* 文字 */
--text-primary:  #e6edf3;  /* 主要文字 */
--text-secondary:#8b949e;  /* 次要文字 */
--text-muted:    #484f58;  /* 占位符/禁用 */

/* 强调色 */
--accent-blue:   #58a6ff;  /* 链接/选中 */
--accent-green:  #3fb950;  /* 成功/完成 */
--accent-red:    #f85149;  /* 错误/删除 */
--accent-yellow: #d29922;  /* 警告/运行中 */
--accent-purple: #bc8cff;  /* Agent 标识 */

/* 边框 */
--border-primary:#30363d;
--border-muted:  #21262d;
```

---

## 4. 核心模块设计

### 4.1 聊天模块

#### 4.1.1 消息数据模型

```typescript
// api/types.ts

interface Message {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;                    // 完整文本（最终态）
  parts: MessagePart[];               // 结构化内容块
  created_at: number;
  metadata?: MessageMetadata;
}

type MessagePart =
  | TextPart
  | ToolCallPart
  | ToolResultPart
  | ThinkingPart
  | ImagePart;

interface TextPart {
  type: 'text';
  content: string;
}

interface ToolCallPart {
  type: 'tool_call';
  call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: 'running' | 'completed' | 'error';
  result?: string;
  duration_ms?: number;
  error?: string;
}

interface ThinkingPart {
  type: 'thinking';
  content: string;                    // 推理过程文本
  collapsed: boolean;                 // 是否折叠
}

interface ImagePart {
  type: 'image';
  url: string;                        // base64 data URL 或远程 URL
  mime_type: string;
  alt?: string;
}

interface MessageMetadata {
  agent?: string;                     // 产出此消息的 agent
  model?: string;                     // 使用的模型
  tokens_used?: number;
  iteration?: number;                 // AgentLoop 迭代次数
}
```

#### 4.1.2 SSE 事件流

```
// 客户端连接: GET /api/chat/events?session_id=X

// 事件类型：
event: text_delta
data: {"delta": "根据分析", "message_id": "msg_123"}

event: thinking_start
data: {"message_id": "msg_123"}

event: thinking_delta
data: {"delta": "让我先检查...", "message_id": "msg_123"}

event: thinking_done
data: {"message_id": "msg_123"}

event: tool_call
data: {"call_id": "tc_1", "tool_name": "read_file", "arguments": {"path": "src/core.py"}, "message_id": "msg_123"}

event: tool_result
data: {"call_id": "tc_1", "status": "completed", "result": "file content...", "duration_ms": 300, "message_id": "msg_123"}

event: assistant_message
data: {"message_id": "msg_123", "content": "完整回复文本", "parts": [...]}

event: image
data: {"url": "data:image/png;base64,...", "mime_type": "image/png", "message_id": "msg_123"}

event: error
data: {"error": "rate limit exceeded", "message_id": "msg_123"}

event: heartbeat
data: {"timestamp": 1234567890}
```

#### 4.1.3 流式渲染流程

```
用户输入 → POST /api/chat/send_async → 返回 204
       ↓
SSE 连接 → GET /api/chat/events?session_id=X
       ↓
┌─────────────────────────────────────────────────┐
│  text_delta → StreamingText.append(delta)       │
│              → 原地更新 DOM (打字机效果)         │
│                                                  │
│  thinking_start → ThinkingBlock 展开             │
│  thinking_delta → ThinkingBlock 追加文本          │
│  thinking_done  → ThinkingBlock 折叠             │
│                                                  │
│  tool_call → ToolCallBlock 显示 ⏳ + 工具名      │
│  tool_result → ToolCallBlock 更新 ✔/✘ + 耗时     │
│                                                  │
│  assistant_message → MarkdownRenderer 渲染       │
│                      StreamingText → 隐藏        │
│                      ThinkingBlock → 可折叠保留   │
│                      ToolCallBlock → 保留状态     │
└─────────────────────────────────────────────────┘
```

### 4.2 Agent 展示模块

#### 4.2.1 Agent 状态卡片（右侧面板）

```typescript
interface AgentState {
  id: string;
  name: string;
  status: 'idle' | 'pending' | 'running' | 'completed' | 'error' | 'skipped';
  started_at?: number;
  completed_at?: number;
  duration_ms?: number;
  current_task?: string;              // 当前正在做什么
  output_summary?: string;            // 输出摘要（前 100 字）
  error?: string;
}
```

卡片布局：
```
┌─────────────────────────┐
│ 🤖 researcher           │  ← 名称 + 图标
│ ⏳ running · 12.3s      │  ← 状态 + 耗时
│ ▓▓▓▓▓▓▓░░░ 70%         │  ← 进度条（可选）
│ 正在分析动量因子表现...    │  ← 当前任务（截断）
└─────────────────────────┘
```

#### 4.2.2 Agent 活动时间线（主聊天区内）

```
┌──────────────────────────────────────────────────┐
│ 📊 Agent: researcher (Layer 1)                    │
│ ┌──────────────────────────────────────────────┐ │
│ │ 🧠 Thinking (折叠)                            │ │
│ │ ├─ ⏳ read_file · src/data.py                 │ │
│ │ ├─ ✔ read_file · 0.3s                        │ │
│ │ ├─ ⏳ execute_python · momentum_analysis.py   │ │
│ │ ├─ ✔ execute_python · 2.1s                    │ │
│ │ └─ 🧠 Thinking done (折叠)                    │ │
│ └──────────────────────────────────────────────┘ │
│ "根据分析结果，动量因子在近 3 个月表现..."           │
└──────────────────────────────────────────────────┘
```

ActivityCluster 组件：
- 标题行：Agent 名称 + Layer + 状态图标
- 内容区：可折叠，包含推理行 + 工具调用行
- 每行：状态图标 + 工具名 + 参数摘要 + 耗时

### 4.3 Workflow DAG 模块

#### 4.3.1 DAG 数据结构

```typescript
interface WorkflowDAG {
  nodes: DAGNode[];
  edges: DAGEdge[];
}

interface DAGNode {
  id: string;                         // agent_id
  label: string;                      // 显示名称
  status: 'pending' | 'running' | 'completed' | 'error' | 'skipped';
  prompt_file: string;
  evidence_criterion: number;
  tools: string[];
  position: { x: number; y: number }; // React Flow 坐标
}

interface DAGEdge {
  id: string;
  source: string;                     // 上游 agent_id
  target: string;                     // 下游 agent_id
  label?: string;
}
```

#### 4.3.2 DAG 可视化（React Flow）

```
┌──────────────────────────────────────────────────────┐
│  Workflow: goal_factor_research                       │
│  "因子研究工作流"                                      │
│                                                       │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     │
│  │○ researcher│────▶│⏳ data   │────▶│○ factor  │     │
│  └──────────┘     │  quality │     │  analyst │     │
│                   └──────────┘     └────┬─────┘     │
│                                         │            │
│                                    ┌────▼─────┐     │
│                                    │○ risk    │     │
│                                    │  review  │     │
│                                    └──────────┘     │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  Objective: [研究动量因子在A股的表现...]       │     │
│  │  [▶ 启动]  [⏸ 暂停]  [🔄 恢复]              │     │
│  └─────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

节点交互：
- 点击节点 → 弹出详情弹窗（prompt/条件/tools/evidence_criterion）
- 运行中节点 → 脉冲动画 + 边高亮
- 完成节点 → 绿色 ✓
- 错误节点 → 红色 ✗ + 点击查看错误

#### 4.3.3 坐标自动布局

使用 `dagre` 或 `@xyflow/react` 的内置布局算法，根据 DAG 拓扑自动计算节点坐标：
- 同一层的节点水平排列
- 层间垂直间距固定
- 节点宽度根据名称自适应

### 4.4 Goal 面板模块

```
┌─────────────────────────┐
│ 🎯 目标进度              │
│                         │
│ 研究动量因子在A股的表现   │  ← 目标描述
│                         │
│ ████████░░░░ 66%        │  ← 进度条
│                         │
│ 标准清单:                │
│ ✔ 收集市场数据           │  ← 已覆盖
│ ○ 分析因子表现           │  ← 未覆盖
│ ✔ 风控审查              │  ← 已覆盖
│                         │
│ 📎 4 条 Evidence        │
│                         │
│ [⏸ 暂停]  [🔄 恢复]     │
└─────────────────────────┘
```

### 4.5 输入框（Composer）

```
┌──────────────────────────────────────────────────┐
│ 👤 输入消息...                                     │
│                                                  │
│ @workspace    # @mention 工作区文件                │
│ @agent_name   # @mention 特定 agent               │
│                                                  │
│ 📎 附件  📷 图片  [发送]                          │
└──────────────────────────────────────────────────┘
```

功能：
- 支持 @mention（文件搜索、agent 列表）
- 支持图片粘贴（Ctrl+V）和拖拽上传
- 支持 Shift+Enter 换行
- 发送后清空输入框
- 历史上箭头翻阅

---

## 5. 认证设计

### 5.1 数据库

```sql
-- users.db
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,       -- bcrypt
    display_name TEXT,
    avatar_url TEXT,
    created_at REAL DEFAULT (unixepoch()),
    last_login REAL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE web_sessions (
    id TEXT PRIMARY KEY,               -- UUID
    user_id INTEGER REFERENCES users(id),
    title TEXT DEFAULT '新会话',
    created_at REAL DEFAULT (unixepoch()),
    updated_at REAL DEFAULT (unixepoch()),
    is_active INTEGER DEFAULT 1
);

CREATE TABLE web_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES web_sessions(id),
    role TEXT NOT NULL,                -- user/assistant/system
    content TEXT,
    parts_json TEXT,                   -- JSON MessagePart[]
    created_at REAL DEFAULT (unixepoch()),
    metadata_json TEXT
);

CREATE INDEX idx_messages_session ON web_messages(session_id, created_at);
```

### 5.2 认证流程

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  前端     │     │  后端     │     │  SQLite  │
│          │     │          │     │          │
│ POST     │────▶│ 验证密码  │────▶│ 查询用户  │
│ /login   │     │ 生成 JWT │     │          │
│          │◀────│          │◀────│          │
│ 存储     │     │          │     │          │
│ token    │     │          │     │          │
│          │     │          │     │          │
│ GET      │────▶│ 验证 JWT │     │          │
│ /api/*   │     │          │     │          │
│          │◀────│ 响应数据  │     │          │
└──────────┘     └──────────┘     └──────────┘
```

### 5.3 JWT 配置

```python
# auth 配置
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
```

---

## 6. API 端点清单

### 6.1 认证 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/auth/register` | POST | `{username, password, display_name?}` | `{user_id, username}` | 注册 |
| `/api/auth/login` | POST | `{username, password}` | `{access_token, refresh_token, user}` | 登录 |
| `/api/auth/me` | GET | — | `{user}` | 当前用户 |
| `/api/auth/refresh` | POST | `{refresh_token}` | `{access_token}` | 刷新 token |

### 6.2 聊天 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/chat/send` | POST | `{session_id, content, images?}` | `{message_id, reply}` | 同步发送 |
| `/api/chat/send_async` | POST | `{session_id, content, images?}` | 204 | 异步发送 |
| `/api/chat/events` | GET | `?session_id=X` | SSE stream | 实时事件流 |
| `/api/chat/history` | GET | `?session_id=X&limit=50&before=msg_id` | `{messages[]}` | 历史消息 |
| `/api/chat/upload` | POST | multipart form | `{image_url, mime_type}` | 上传图片 |
| `/api/chat/message` | DELETE | `?message_id=X` | 204 | 删除消息 |

### 6.3 会话 API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/session/create` | POST | `{title?}` | `{session_id, title}` | 创建会话 |
| `/api/session/list` | GET | `?limit=50` | `{sessions[]}` | 列出会话 |
| `/api/session/update` | PATCH | `{session_id, title}` | `{ok}` | 更新标题 |
| `/api/session/delete` | DELETE | `?session_id=X` | 204 | 删除会话 |

### 6.4 Goal/Workflow API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/goal/start` | POST | `{session_id, objective, risk_tier?}` | `{goal_id}` | 创建 goal |
| `/api/goal/status` | GET | `?session_id=X` | `{goal}` | goal 状态 |
| `/api/goal/workflow/start` | POST | `{session_id, workflow_name, objective}` | `{goal_id}` | 启动 workflow |
| `/api/goal/workflow/status` | GET | `?goal_id=X` | `{progress}` | workflow 进度 |
| `/api/goal/workflow/events` | GET | `?goal_id=X` | SSE stream | workflow 事件 |
| `/api/goal/workflow/pause` | POST | `{goal_id, immediate?}` | `{ok}` | 暂停 |
| `/api/goal/workflow/resume` | POST | `{goal_id}` | `{ok}` | 恢复 |
| `/api/goal/workflow/list` | GET | — | `{workflows[]}` | 列出 presets |
| `/api/goal/workflow/{name}/dag` | GET | — | `{dag}` | DAG 结构 |
| `/api/goal/workflow/{name}/agents` | GET | — | `{agents[]}` | agent 详情 |

### 6.5 Agent API

| 端点 | 方法 | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| `/api/agent/list` | GET | — | `{agents[]}` | 可用 agents |
| `/api/agent/status` | GET | `?goal_id=X` | `{agents_status[]}` | 运行状态 |

---

## 7. 开发计划

### 阶段划分

| 阶段 | 内容 | 天数 | 依赖 |
|---|---|---|---|
| **P1: 脚手架** | React 项目初始化 + Vite + Tailwind + 路由 + 布局骨架 + 认证 | 3 | 无 |
| **P2: 聊天核心** | 消息列表 + 流式渲染 + Markdown + 输入框 + SSE | 4 | P1 |
| **P3: 工具调用** | ToolCallBlock + DiffBlock + ThinkingBlock + 图片 | 3 | P2 |
| **P4: Agent 面板** | AgentCard + AgentTimeline + ActivityCluster | 2 | P2 |
| **P5: Workflow DAG** | React Flow + DAGNode + 启动 + 实时状态 | 3 | P2 |
| **P6: Goal + 后端** | GoalPanel + 后端 chat API + session 管理 | 2 | P1 |
| **P7: 集成测试** | 全链路测试 + 优化 + 打包 | 2 | P1-P6 |
| **总计** | | **~19 天** | |

### P1 详细任务

1. 初始化 React 项目 (`npm create vite@latest`)
2. 安装依赖 (tailwindcss, zustand, react-router-dom, lucide-react, radix-ui)
3. 配置 Tailwind 深色主题
4. 实现 AppShell 三栏布局
5. 实现 TopBar + Sidebar + RightPanel
6. 实现 LoginPage + RegisterPage
7. 实现 AuthGuard 路由守卫
8. 实现 API Client (fetch wrapper + JWT 注入)
9. 实现 Zustand auth store
10. 后端 auth.py (register/login/me/refresh)
11. 后端 JWT 中间件

### P2 详细任务

1. 实现 SSE 连接管理器
2. 实现 chat store (messages, streaming state)
3. 实现 MessageList (虚拟滚动)
4. 实现 MessageBubble (用户消息)
5. 实现 AssistantMessage (助手消息)
6. 实现 StreamingText (打字机效果)
7. 实现 MarkdownRenderer
8. 实现 Composer (@mention + 图片)
9. 后端 chat.py (send/send_async/events/history)
10. 后端 session.py (create/list/update/delete)

---

## 8. 文件变更清单

### 新增文件

| 文件 | 说明 |
|---|---|
| `webui/frontend/` | React 项目目录 (~30 个组件文件) |
| `api/routers/auth.py` | 认证 API |
| `api/routers/chat.py` | 聊天 API |
| `api/middleware/auth.py` | JWT 中间件 |
| `docs/web-ui-design.md` | 本文档 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `api/app.py` | 注册 auth/chat router + 静态文件 mount |
| `api/routers/workflow.py` | 增加 DAG/agent 端点 |
| `pyproject.toml` | 新增 python-jose, bcrypt 依赖 |

### 保留文件（后续可移除）

| 文件 | 说明 |
|---|---|
| `webui/routes.py` | 旧 Jinja2 路由 |
| `webui/templates/` | 旧 HTML 模板 |

---

## 9. 待讨论项

1. **图片存储**：base64 inline 还是存文件系统？（建议：小图 base64，大图存 `/tmp/uploads/`）
2. **消息分页**：游标分页还是 offset 分页？（建议：游标，基于 created_at + id）
3. **SSE 重连策略**：指数退避 + 最大重试次数？
4. **WebSocket**：是否需要双向实时通信？（目前 SSE 单向够用）
5. **国际化**：i18n 框架选型？（建议：先硬编码中文，后续用 react-i18next）
6. **构建部署**：Vite build 输出到 `webui/static/`，FastAPI serve 静态文件？
