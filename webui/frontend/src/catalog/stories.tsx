/**
 * Catalog registry — Storybook-style index of all UI components with sample props.
 *
 * Mounted via:
 *   /catalog         → index of all stories
 *   /catalog/:name   → single story with isolated render
 *
 * Gated by `import.meta.env.DEV` so it doesn't ship in production builds
 * unless explicitly enabled with `VITE_ENABLE_CATALOG=1`.
 */

import type { ReactNode } from 'react'
import { Bot, Workflow, Target, MessageSquare, Layers, Inbox } from 'lucide-react'

import { Badge } from '../components/common/Badge'
import { Spinner } from '../components/common/Spinner'
import { EmptyState } from '../components/common/EmptyState'
import { Skeleton } from '../components/common/Skeleton'
import { CommandPalette } from '../components/common/CommandPalette'
import { NavPopover } from '../components/common/NavPopover'
import { ConfirmDialog } from '../components/common/ConfirmDialog'

import { MessageBubble } from '../components/chat/MessageBubble'
import { AssistantMessage } from '../components/chat/AssistantMessage'
import { StreamingText } from '../components/chat/StreamingText'
import { MarkdownRenderer } from '../components/chat/MarkdownRenderer'
import { ToolCallBlock } from '../components/chat/ToolCallBlock'
import { FileEditBlock } from '../components/chat/FileEditBlock'
import { TableBlock } from '../components/chat/TableBlock'
import { ChartBlock } from '../components/chat/ChartBlock'
import { ThinkingBlock } from '../components/chat/ThinkingBlock'
import { ImageBlock } from '../components/chat/ImageBlock'

import { AgentItem } from '../components/agent/AgentItem'
import type { Agent } from '../stores/agents'

import { DAGNode, type DAGNodeData } from '../components/workflow/DAGNode'
import { DAGProgressBar } from '../components/workflow/DAGProgressBar'
import { DAGToolbar } from '../components/workflow/DAGToolbar'

import type { Message } from '../stores/chat'

// ────────────────────────── Types ──────────────────────────

export type StoryCategory = 'common' | 'chat' | 'agent' | 'workflow'

export interface Story {
  name: string
  title: string
  description: string
  category: StoryCategory
  icon?: typeof Inbox
  render: () => ReactNode
}

// ────────────────────────── Sample data ──────────────────────────

const userMessage: Message = {
  id: 'msg-user-1',
  session_id: 'demo',
  role: 'user',
  parts: [{ type: 'text', text: '请帮我分析 AAPL 最近 30 天的动量因子。' }],
  created_at: Date.now() / 1000,
}

const assistantMessage: Message = {
  id: 'msg-asst-1',
  session_id: 'demo',
  role: 'assistant',
  parts: [
    { type: 'text', text: '好的，我先读取 AAPL 的历史数据，然后计算 30 日动量。' },
    {
      type: 'tool_call',
      id: 'tc-1',
      name: 'load_market_data',
      arguments: '{"symbol": "AAPL", "period": "30d"}',
      status: 'done',
      result: '{"rows": 30, "start": "2024-01-01", "end": "2024-01-30"}',
    },
    {
      type: 'text',
      text: '数据加载完成，开始计算。\n\n**结果**：IC = 0.08, Sharpe = 1.42',
    },
  ],
  created_at: Date.now() / 1000,
}

const makeAgent = (
  id: string,
  name: string,
  status: Agent['status'],
  description: string,
  iteration = 0
): Agent => ({
  id,
  session_id: 'demo',
  status,
  name,
  description,
  created_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
  tool_calls_count: 0,
  compaction_count: 0,
  context_tokens: 0,
  context_tokens_limit: 8000,
  iterations_detail: [],
})

// ────────────────────────── Stories ──────────────────────────

export const stories: Story[] = [
  // ───── common ─────
  {
    name: 'badge-default',
    title: 'Badge — 默认',
    description: '基础灰色徽章',
    category: 'common',
    render: () => (
      <div className="flex gap-2">
        <Badge>默认</Badge>
        <Badge variant="success">已完成</Badge>
        <Badge variant="warning">运行中</Badge>
        <Badge variant="error">失败</Badge>
      </div>
    ),
  },
  {
    name: 'spinner',
    title: 'Spinner',
    description: '加载旋转器（3 种尺寸）',
    category: 'common',
    render: () => (
      <div className="flex items-center gap-6">
        <Spinner size="sm" />
        <Spinner size="md" />
        <Spinner size="lg" />
      </div>
    ),
  },
  {
    name: 'empty-state',
    title: 'EmptyState',
    description: '空状态占位（自定义 icon + action）',
    category: 'common',
    render: () => (
      <div className="h-64 w-80">
        <EmptyState
          icon={<Inbox className="h-12 w-12" />}
          title="暂无会话"
          description="点击下方按钮创建你的第一个会话"
          action={
            <button className="rounded-lg bg-primary-600 px-4 py-2 text-sm text-white hover:bg-primary-700">
              创建会话
            </button>
          }
        />
      </div>
    ),
  },
  {
    name: 'skeleton',
    title: 'Skeleton',
    description: '骨架屏占位',
    category: 'common',
    render: () => (
      <div className="w-80 space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    ),
  },
  {
    name: 'nav-popover',
    title: 'NavPopover',
    description: '侧边栏 hover 弹出（trigger + content）',
    category: 'common',
    render: () => (
      <div className="flex h-32 items-start gap-2 p-4">
        <NavPopover
          trigger={
            <button className="rounded-lg bg-slate-800 p-2 text-slate-400 hover:bg-slate-700">
              <Workflow className="h-5 w-5" />
            </button>
          }
          side="right"
        >
          <div className="text-sm font-medium text-slate-100">工作流</div>
          <div className="mt-1 text-xs text-slate-400">
            DAG 视图与节点详情
          </div>
        </NavPopover>
      </div>
    ),
  },
  {
    name: 'confirm-dialog-default',
    title: 'ConfirmDialog — Default',
    description: '确认弹窗（默认样式）',
    category: 'common',
    render: () => (
      <div className="h-64">
        <ConfirmDialog
          open={true}
          onOpenChange={() => {}}
          title="删除会话？"
          description="此操作不可撤销。"
          onConfirm={() => {}}
        />
      </div>
    ),
  },
  {
    name: 'confirm-dialog-danger',
    title: 'ConfirmDialog — Danger',
    description: '确认弹窗（危险操作）',
    category: 'common',
    render: () => (
      <div className="h-64">
        <ConfirmDialog
          open={true}
          onOpenChange={() => {}}
          title="停止所有 Agent？"
          description="所有正在运行的策略将被强制终止。"
          variant="danger"
          confirmLabel="强制停止"
          onConfirm={() => {}}
        />
      </div>
    ),
  },
  {
    name: 'command-palette',
    title: 'CommandPalette',
    description: 'Cmd+K 调色板（命令搜索）',
    category: 'common',
    render: () => (
      <div className="h-96">
        <CommandPalette />
      </div>
    ),
  },

  // ───── chat ─────
  {
    name: 'message-bubble-user',
    title: 'MessageBubble — 用户',
    description: '用户消息气泡（右对齐 + 主色）',
    category: 'chat',
    icon: MessageSquare,
    render: () => (
      <div className="w-96 bg-slate-950 p-4">
        <MessageBubble message={userMessage} />
      </div>
    ),
  },
  {
    name: 'assistant-message',
    title: 'AssistantMessage — 工具调用',
    description: '助手消息 + tool_call 块',
    category: 'chat',
    icon: Bot,
    render: () => (
      <div className="w-[600px] bg-slate-950 p-4">
        <AssistantMessage message={assistantMessage} isStreaming={false} />
      </div>
    ),
  },
  {
    name: 'streaming-text',
    title: 'StreamingText',
    description: '流式文本渲染（带光标）',
    category: 'chat',
    render: () => (
      <div className="w-96 bg-slate-950 p-4 text-sm text-slate-200">
        <StreamingText text="正在计算 alpha 因子..." isDone={false} />
      </div>
    ),
  },
  {
    name: 'streaming-text-done',
    title: 'StreamingText — Done',
    description: '流式完成状态（无光标）',
    category: 'chat',
    render: () => (
      <div className="w-96 bg-slate-950 p-4 text-sm text-slate-200">
        <StreamingText text="流式响应已完成。" isDone={true} />
      </div>
    ),
  },
  {
    name: 'markdown-renderer',
    title: 'MarkdownRenderer',
    description: 'Markdown 渲染（标题+列表+代码+表格）',
    category: 'chat',
    render: () => (
      <div className="w-[600px] rounded-lg border border-slate-700 bg-slate-900 p-4 text-slate-200">
        <MarkdownRenderer
          content={
            '# 分析报告\n\n## 数据概览\n\n- 总样本数：**30**\n- IC = 0.08\n\n```python\nprint("hello")\n```\n\n| Metric | Value |\n|---|---|\n| Sharpe | 1.42 |'
          }
        />
      </div>
    ),
  },
  {
    name: 'tool-call-running',
    title: 'ToolCallBlock — Running',
    description: '工具调用（运行中）',
    category: 'chat',
    render: () => (
      <div className="w-[500px] bg-slate-950 p-4">
        <ToolCallBlock
          toolCall={{
            type: 'tool_call',
            id: 'tc-running',
            name: 'compute_alpha',
            arguments: '{"symbol": "AAPL", "period": 30}',
            status: 'running',
          }}
          startTime={Date.now() - 1500}
        />
      </div>
    ),
  },
  {
    name: 'tool-call-done',
    title: 'ToolCallBlock — Done',
    description: '工具调用（已完成 + 结果）',
    category: 'chat',
    render: () => (
      <div className="w-[500px] bg-slate-950 p-4">
        <ToolCallBlock
          toolCall={{
            type: 'tool_call',
            id: 'tc-done',
            name: 'backtest',
            arguments: '{"strategy": "momentum"}',
            status: 'done',
            result: '{"sharpe": 1.42, "max_dd": 0.18}',
          }}
          startTime={Date.now() - 5000}
        />
      </div>
    ),
  },
  {
    name: 'tool-call-error',
    title: 'ToolCallBlock — Error',
    description: '工具调用（失败）',
    category: 'chat',
    render: () => (
      <div className="w-[500px] bg-slate-950 p-4">
        <ToolCallBlock
          toolCall={{
            type: 'tool_call',
            id: 'tc-err',
            name: 'load_data',
            arguments: '{}',
            status: 'error',
            result: 'Network error: connection timed out',
          }}
          startTime={Date.now() - 3000}
        />
      </div>
    ),
  },
  {
    name: 'file-edit-block',
    title: 'FileEditBlock',
    description: '文件编辑 diff（统一 diff）',
    category: 'chat',
    render: () => (
      <div className="w-[700px] bg-slate-950 p-4">
        <FileEditBlock
          fileEdit={{
            type: 'file_edit',
            file_path: 'src/strategy_research/core/alpha.py',
            old_content:
              'def compute_alpha(df):\n    return df["close"].pct_change(20)',
            new_content:
              'def compute_alpha(df, period: int = 20):\n    return df["close"].pct_change(period)',
          }}
        />
      </div>
    ),
  },
  {
    name: 'table-block',
    title: 'TableBlock',
    description: '表格渲染（截断 + CSV 下载）',
    category: 'chat',
    render: () => (
      <div className="w-[600px] bg-slate-950 p-4">
        <TableBlock
          table={{
            type: 'table',
            title: 'Top 10 Alpha Factors',
            headers: ['Symbol', 'IC', 'Sharpe', 'Max DD'],
            rows: [
              ['AAPL', '0.082', '1.42', '0.18'],
              ['MSFT', '0.075', '1.31', '0.21'],
              ['GOOGL', '0.069', '1.18', '0.24'],
              ['AMZN', '0.063', '1.09', '0.27'],
            ],
          }}
        />
      </div>
    ),
  },
  {
    name: 'chart-bar',
    title: 'ChartBlock — Bar',
    description: '柱状图',
    category: 'chat',
    render: () => (
      <div className="h-64 w-[500px] bg-slate-950 p-4">
        <ChartBlock
          chart={{
            type: 'chart',
            chartType: 'bar',
            title: 'Monthly Returns',
            data: [
              { label: 'Jan', value: 0.05 },
              { label: 'Feb', value: -0.02 },
              { label: 'Mar', value: 0.08 },
              { label: 'Apr', value: 0.12 },
              { label: 'May', value: -0.03 },
              { label: 'Jun', value: 0.07 },
            ],
          }}
        />
      </div>
    ),
  },
  {
    name: 'chart-line',
    title: 'ChartBlock — Line',
    description: '折线图',
    category: 'chat',
    render: () => (
      <div className="h-64 w-[500px] bg-slate-950 p-4">
        <ChartBlock
          chart={{
            type: 'chart',
            chartType: 'line',
            title: 'Cumulative Returns',
            data: [
              { label: 'W1', value: 0.0 },
              { label: 'W2', value: 0.02 },
              { label: 'W3', value: 0.05 },
              { label: 'W4', value: 0.04 },
              { label: 'W5', value: 0.08 },
              { label: 'W6', value: 0.11 },
              { label: 'W7', value: 0.13 },
              { label: 'W8', value: 0.15 },
            ],
          }}
        />
      </div>
    ),
  },
  {
    name: 'thinking-block',
    title: 'ThinkingBlock',
    description: '思考块（可折叠）',
    category: 'chat',
    render: () => (
      <div className="w-[500px] bg-slate-950 p-4">
        <ThinkingBlock
          text={
            '1. 分析用户问题 → 检索相关策略\n2. 加载 AAPL 历史数据\n3. 计算 30 日动量\n4. 评估 IC / Sharpe'
          }
          collapsed={false}
        />
      </div>
    ),
  },
  {
    name: 'image-block',
    title: 'ImageBlock',
    description: '图片块（SVG placeholder）',
    category: 'chat',
    render: () => (
      <div className="w-80 bg-slate-950 p-4">
        <ImageBlock
          src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='400' height='200'><rect width='400' height='200' fill='%23334155'/><text x='200' y='110' fill='%23cbd5e1' text-anchor='middle' font-size='24'>Sample Chart</text></svg>"
          alt="Sample data visualization"
        />
      </div>
    ),
  },

  // ───── agent ─────
  {
    name: 'agent-item-idle',
    title: 'AgentItem — Idle',
    description: 'Agent 列表项（待命）',
    category: 'agent',
    icon: Bot,
    render: () => (
      <div className="w-96 bg-slate-900 p-4">
        <AgentItem agent={makeAgent('a-1', 'Data Loader', 'pending', 'Loads market data from Tushare / Eastmoney')} />
      </div>
    ),
  },
  {
    name: 'agent-item-running',
    title: 'AgentItem — Running',
    description: 'Agent 列表项（运行中）',
    category: 'agent',
    icon: Bot,
    render: () => (
      <div className="w-96 bg-slate-900 p-4">
        <AgentItem agent={makeAgent('a-2', 'Alpha Researcher', 'running', 'Computes alpha factors and evaluates IC', 3)} />
      </div>
    ),
  },
  {
    name: 'agent-item-completed',
    title: 'AgentItem — Completed',
    description: 'Agent 列表项（已完成）',
    category: 'agent',
    icon: Bot,
    render: () => (
      <div className="w-96 bg-slate-900 p-4">
        <AgentItem agent={makeAgent('a-3', 'Backtest Engine', 'completed', 'Runs vectorized backtest over historical data', 5)} />
      </div>
    ),
  },

  // ───── workflow ─────
  {
    name: 'dag-progress-bar',
    title: 'DAGProgressBar',
    description: 'DAG 执行进度条（50%）',
    category: 'workflow',
    icon: Workflow,
    render: () => (
      <div className="w-[600px]">
        <DAGProgressBar progress={0.5} total={4} completed={2} />
      </div>
    ),
  },
  {
    name: 'dag-progress-bar-complete',
    title: 'DAGProgressBar — Complete',
    description: 'DAG 执行进度条（100%）',
    category: 'workflow',
    icon: Workflow,
    render: () => (
      <div className="w-[600px]">
        <DAGProgressBar progress={1.0} total={4} completed={4} />
      </div>
    ),
  },
  {
    name: 'dag-toolbar',
    title: 'DAGToolbar',
    description: 'DAG 工具栏（running 状态）',
    category: 'workflow',
    render: () => (
      <div className="w-[600px]">
        <DAGToolbar
          workflowName="动量策略研究"
          status="running"
          onStart={() => {}}
          onPause={() => {}}
          onResume={() => {}}
          onReset={() => {}}
        />
      </div>
    ),
  },
  {
    name: 'dag-toolbar-completed',
    title: 'DAGToolbar — Completed',
    description: 'DAG 工具栏（已完成状态）',
    category: 'workflow',
    render: () => (
      <div className="w-[600px]">
        <DAGToolbar
          workflowName="价值因子研究"
          status="completed"
          onStart={() => {}}
          onPause={() => {}}
          onResume={() => {}}
          onReset={() => {}}
        />
      </div>
    ),
  },
  {
    name: 'dag-node-pending',
    title: 'DAGNode — Pending',
    description: 'DAG 节点（待执行）',
    category: 'workflow',
    icon: Workflow,
    render: () => <DAGNodeCard status="pending" label="Plan Research" />,
  },
  {
    name: 'dag-node-running',
    title: 'DAGNode — Running',
    description: 'DAG 节点（运行中）',
    category: 'workflow',
    icon: Workflow,
    render: () => <DAGNodeCard status="running" label="Compute Alpha" />,
  },
  {
    name: 'dag-node-completed',
    title: 'DAGNode — Completed',
    description: 'DAG 节点（已完成）',
    category: 'workflow',
    icon: Workflow,
    render: () => <DAGNodeCard status="completed" label="Backtest Strategy" />,
  },
  {
    name: 'dag-node-failed',
    title: 'DAGNode — Failed',
    description: 'DAG 节点（失败）',
    category: 'workflow',
    icon: Workflow,
    render: () => <DAGNodeCard status="failed" label="Generate Report" />,
  },
]

// ────────────────────────── DAGNode mock helper ──────────────────────────

// React Flow 的 DAGNode 依赖 NodeProps + Handle（在 ReactFlow context 中）
// Catalog 中没有 context — 用简化版复制视觉风格，绕开 Handle 报错
function DAGNodeCard({ status, label }: { status: 'pending' | 'running' | 'completed' | 'failed'; label: string }) {
  const STATUS_CONFIG: Record<string, { borderColor: string; bgColor: string; textColor: string; iconColor: string; label: string; emoji: string }> = {
    pending: { borderColor: 'border-slate-600', bgColor: 'bg-slate-800/50', textColor: 'text-slate-500', iconColor: 'text-slate-500', label: 'Pending', emoji: '○' },
    running: { borderColor: 'border-blue-500', bgColor: 'bg-blue-500/10', textColor: 'text-blue-400', iconColor: 'text-blue-400 animate-spin', label: 'Running', emoji: '⟳' },
    completed: { borderColor: 'border-emerald-500', bgColor: 'bg-emerald-500/10', textColor: 'text-emerald-400', iconColor: 'text-emerald-400', label: 'Completed', emoji: '✓' },
    failed: { borderColor: 'border-red-500', bgColor: 'bg-red-500/10', textColor: 'text-red-400', iconColor: 'text-red-400', label: 'Failed', emoji: '✗' },
  }
  const c = STATUS_CONFIG[status]
  return (
    <div className="rounded-lg border-2 border-slate-700 bg-slate-800 p-3 min-w-[180px]">
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-base ${c.iconColor}`}>{c.emoji}</span>
        <span className={`text-xs font-medium ${c.textColor}`}>{c.label}</span>
      </div>
      <div className="text-sm font-medium text-slate-100 truncate">{label}</div>
    </div>
  )
}

// ────────────────────────── Helpers ──────────────────────────

export function findStory(name: string): Story | undefined {
  return stories.find((s) => s.name === name)
}

export function storiesByCategory(): Record<StoryCategory, Story[]> {
  const grouped: Record<StoryCategory, Story[]> = {
    common: [],
    chat: [],
    agent: [],
    workflow: [],
  }
  for (const story of stories) {
    grouped[story.category].push(story)
  }
  return grouped
}

export const categoryLabels: Record<StoryCategory, string> = {
  common: 'Common',
  chat: 'Chat',
  agent: 'Agent',
  workflow: 'Workflow',
}

export const categoryIcons: Record<StoryCategory, typeof Inbox> = {
  common: Layers,
  chat: MessageSquare,
  agent: Bot,
  workflow: Workflow,
}