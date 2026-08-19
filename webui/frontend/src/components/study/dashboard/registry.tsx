/**
 * Widget Registry — maps widget type IDs to their definitions.
 *
 * Each entry provides metadata (label, icon, defaults) and the React
 * component to render.  The component receives a uniform `WidgetProps`
 * shape; wrappers extract the relevant fields from `summary`.
 */
import { useState } from 'react'
import type { WidgetDef, WidgetProps } from './types'

// ── Lazy-load heavy components to keep initial bundle small ──────
import { lazy } from 'react'

const ObjectiveProgress = lazy(() =>
  import('../ObjectiveProgress').then(m => ({ default: m.ObjectiveProgress })),
)
const RoundHistory = lazy(() =>
  import('../RoundHistory').then(m => ({ default: m.RoundHistory })),
)
const MetricsCompare = lazy(() =>
  import('../MetricsCompare').then(m => ({ default: m.MetricsCompare })),
)
const MetricsTrendChart = lazy(() =>
  import('../MetricsTrendChart').then(m => ({ default: m.MetricsTrendChart })),
)
const BudgetBar = lazy(() =>
  import('../BudgetBar').then(m => ({ default: m.BudgetBar })),
)
const ScoreboardMini = lazy(() =>
  import('../ScoreboardMini').then(m => ({ default: m.ScoreboardMini })),
)
const AgentFlowCanvas = lazy(() =>
  import('../AgentFlowCanvas').then(m => ({ default: m.AgentFlowCanvas })),
)
const AgentChatLog = lazy(() =>
  import('../AgentChatLog').then(m => ({ default: m.AgentChatLog })),
)

// ── New widgets (Phase C) — stub placeholders for now ────────────
const LiveActivity = lazy(() =>
  import('./widgets/LiveActivity').then(m => ({ default: m.LiveActivity })),
)
const EventTimeline = lazy(() =>
  import('./widgets/EventTimeline').then(m => ({ default: m.EventTimeline })),
)
const KnowledgeView = lazy(() =>
  import('./widgets/KnowledgeView').then(m => ({ default: m.KnowledgeView })),
)
const TodosView = lazy(() =>
  import('./widgets/TodosView').then(m => ({ default: m.TodosView })),
)
const JournalView = lazy(() =>
  import('./widgets/JournalView').then(m => ({ default: m.JournalView })),
)

// ── Wrapper components (adapt existing props → WidgetProps) ───────

function ObjectiveWrapper({ studyId, summary }: WidgetProps) {
  const g = summary.goal_snapshot
  return (
    <ObjectiveProgress
      objective={summary.objective}
      progressPercent={g?.progress_percent}
      evidenceCount={g?.evidence_count}
      criteria={g?.criteria}
      studyId={studyId}
    />
  )
}

function RoundHistoryWrapper({ studyId, summary }: WidgetProps) {
  return (
    <RoundHistory
      rounds={summary.recent_rounds ?? []}
      currentRound={summary.current_round}
      studyId={studyId}
    />
  )
}

function MetricsCompareWrapper({ summary }: WidgetProps) {
  return <MetricsCompare rounds={summary.recent_rounds ?? []} />
}

function MetricsTrendWrapper({ summary }: WidgetProps) {
  return <MetricsTrendChart rounds={summary.recent_rounds ?? []} />
}

function BudgetWrapper({ summary }: WidgetProps) {
  const b = summary.budget as {
    budget_used_turns?: number
    budget_used_time_s?: number
    budget_turn?: number | null
    budget_time_seconds?: number | null
  } | null
  if (!b) return null
  return (
    <BudgetBar
      usedTurns={b.budget_used_turns}
      totalTurns={b.budget_turn}
      usedTimeS={b.budget_used_time_s}
      totalTimes={b.budget_time_seconds}
    />
  )
}

function ScoreboardWrapper({ summary }: WidgetProps) {
  return <ScoreboardMini scoreboard={summary.scoreboard ?? []} />
}

function DAGFlowWrapper({ studyId, summary }: WidgetProps) {
  const [selectedRound, setSelectedRound] = useState(summary.current_round || 1)
  return (
    <AgentFlowCanvas
      studyId={studyId}
      selectedRound={selectedRound}
      onSelectedRoundChange={setSelectedRound}
      totalRounds={summary.current_round}
    />
  )
}

function AgentChatWrapper({ studyId, summary }: WidgetProps) {
  const [selectedRound, setSelectedRound] = useState(summary.current_round || 1)
  return (
    <AgentChatLog
      studyId={studyId}
      selectedRound={selectedRound}
      onSelectedRoundChange={setSelectedRound}
      totalRounds={summary.current_round}
    />
  )
}

// ── Inline widgets (simple extractors from summary) ──────────────

function DirectivesInline({ summary }: WidgetProps) {
  return (
    <div className="text-xs text-slate-400 space-y-1">
      <div>策略: {summary.strategy_name ?? '—'}</div>
      <div>轮次: {summary.current_round} / {summary.max_rounds ?? '∞'}</div>
      <div>状态: {summary.execution_status}</div>
    </div>
  )
}

function TaskInfoInline({ summary }: WidgetProps) {
  return (
    <div className="text-xs text-slate-400 space-y-1">
      {summary.workspace_path && (
        <div className="truncate" title={summary.workspace_path}>
          📁 {summary.workspace_path}
        </div>
      )}
      {summary.created_at && <div>创建: {new Date(summary.created_at).toLocaleString()}</div>}
      {summary.updated_at && <div>更新: {new Date(summary.updated_at).toLocaleString()}</div>}
      {summary.completed_at && <div>完成: {new Date(summary.completed_at).toLocaleString()}</div>}
    </div>
  )
}

// ── Registry ─────────────────────────────────────────────────────

export const WIDGET_REGISTRY: Record<string, WidgetDef> = {
  'live-activity': {
    id: 'live-activity',
    label: '实时活动',
    icon: '⚡',
    defaultEnabled: true,
    defaultSpan: 12,
    component: LiveActivity,
  },
  'objective': {
    id: 'objective',
    label: '目标进度',
    icon: '🎯',
    defaultEnabled: true,
    defaultSpan: 8,
    component: ObjectiveWrapper,
  },
  'dag-flow': {
    id: 'dag-flow',
    label: '研究流程',
    icon: '🔀',
    defaultEnabled: true,
    defaultSpan: 12,
    component: DAGFlowWrapper,
  },
  'round-history': {
    id: 'round-history',
    label: '轮次历史',
    icon: '📋',
    defaultEnabled: true,
    defaultSpan: 6,
    component: RoundHistoryWrapper,
  },
  'metrics-compare': {
    id: 'metrics-compare',
    label: '指标对比',
    icon: '📊',
    defaultEnabled: true,
    defaultSpan: 6,
    component: MetricsCompareWrapper,
  },
  'metrics-trend': {
    id: 'metrics-trend',
    label: '指标趋势',
    icon: '📈',
    defaultEnabled: false,
    defaultSpan: 12,
    component: MetricsTrendWrapper,
  },
  'budget': {
    id: 'budget',
    label: '预算',
    icon: '⏱',
    defaultEnabled: false,
    defaultSpan: 12,
    component: BudgetWrapper,
  },
  'scoreboard': {
    id: 'scoreboard',
    label: '杠杆精度',
    icon: '🎯',
    defaultEnabled: false,
    defaultSpan: 12,
    component: ScoreboardWrapper,
  },
  'event-timeline': {
    id: 'event-timeline',
    label: '事件流',
    icon: '📜',
    defaultEnabled: false,
    defaultSpan: 12,
    component: EventTimeline,
  },
  'directives': {
    id: 'directives',
    label: '指令',
    icon: '💬',
    defaultEnabled: false,
    defaultSpan: 4,
    component: DirectivesInline,
  },
  'task-info': {
    id: 'task-info',
    label: '任务信息',
    icon: 'ℹ️',
    defaultEnabled: false,
    defaultSpan: 4,
    component: TaskInfoInline,
  },
  'agent-chat': {
    id: 'agent-chat',
    label: 'Agent 群聊',
    icon: '💬',
    defaultEnabled: false,
    defaultSpan: 12,
    component: AgentChatWrapper,
  },
  'knowledge': {
    id: 'knowledge',
    label: '知识库',
    icon: '📚',
    defaultEnabled: false,
    defaultSpan: 12,
    component: KnowledgeView,
  },
  'todos': {
    id: 'todos',
    label: '待办',
    icon: '✅',
    defaultEnabled: false,
    defaultSpan: 12,
    component: TodosView,
  },
  'journal': {
    id: 'journal',
    label: '日志',
    icon: '📝',
    defaultEnabled: false,
    defaultSpan: 12,
    component: JournalView,
  },
}

/** Ordered list of widget type IDs (for rendering the picker) */
export const WIDGET_ORDER = Object.keys(WIDGET_REGISTRY)
