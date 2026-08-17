import { Activity } from 'lucide-react'

type NodeStatus = 'pending' | 'running' | 'done' | 'error'

interface DAGNode {
  id: string
  label: string
  status: NodeStatus
  duration_s?: number
}

interface Props {
  currentRound: number
  totalRounds?: number
  agentStatuses?: Record<string, NodeStatus>
  agentDurations?: Record<string, number>
}

const DEFAULT_AGENTS = [
  { id: 'researcher', label: 'R' },
  { id: 'data_quality', label: 'DQ' },
  { id: 'factor_analyst', label: 'FA' },
  { id: 'strategist', label: 'ST' },
  { id: 'portfolio_construction', label: 'PC' },
  { id: 'risk_controller', label: 'RC' },
  { id: 'attribution_analyst', label: 'AA' },
  { id: 'anti_overfit_analyst', label: 'AO' },
]

const STATUS_CONFIG: Record<NodeStatus, { border: string; bg: string; text: string; shadow: string }> = {
  done: {
    border: 'border-emerald-500',
    bg: 'bg-emerald-500',
    text: 'text-emerald-50',
    shadow: 'shadow-emerald-500/30',
  },
  running: {
    border: 'border-sky-500',
    bg: 'bg-sky-500/20',
    text: 'text-sky-400',
    shadow: 'shadow-sky-500/40',
  },
  error: {
    border: 'border-rose-500',
    bg: 'bg-rose-500/20',
    text: 'text-rose-400',
    shadow: 'shadow-rose-500/40',
  },
  pending: {
    border: 'border-slate-700',
    bg: 'bg-slate-800/40',
    text: 'text-slate-500',
    shadow: 'shadow-transparent',
  },
}

function DAGNodeComponent({ node }: { node: DAGNode }) {
  const config = STATUS_CONFIG[node.status]
  const isDone = node.status === 'done'
  const isRunning = node.status === 'running'
  const isError = node.status === 'error'

  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className={`flex h-8 w-8 items-center justify-center rounded-full border-2 text-[10px] font-bold transition-all duration-200 ${config.border} ${config.bg} ${config.text} ${
          isRunning ? 'animate-pulse' : ''
        }`}
        style={isRunning ? { boxShadow: '0 0 12px rgba(14, 165, 233, 0.45)' } : undefined}
      >
        {isDone ? (
          <span className="text-emerald-50">✓</span>
        ) : isRunning ? (
          <span className="h-2 w-2 animate-ping rounded-full bg-sky-400" />
        ) : isError ? (
          <span className="text-rose-400">✗</span>
        ) : (
          <span className="text-slate-500">·</span>
        )}
      </div>
      <span className={`text-[9px] font-medium ${config.text}`}>{node.label}</span>
      {node.duration_s != null && (
        <span className="text-[8px] text-slate-600">{node.duration_s.toFixed(0)}s</span>
      )}
    </div>
  )
}

function Connector({ status }: { status: NodeStatus }) {
  return (
    <div className="mt-2.5 min-w-0 flex-1">
      <div
        className={`h-0.5 rounded-full ${
          status === 'done'
            ? 'bg-emerald-500/60'
            : status === 'running'
              ? 'animate-pulse bg-sky-500/60'
              : 'border-t border-dashed border-slate-700'
        }`}
      />
    </div>
  )
}

export function DAGVisualization({ currentRound, totalRounds, agentStatuses, agentDurations }: Props) {
  const nodes: DAGNode[] = DEFAULT_AGENTS.map((a) => ({
    id: a.id,
    label: a.label,
    status: agentStatuses?.[a.id] ?? 'pending',
    duration_s: agentDurations?.[a.id],
  }))

  const doneCount = nodes.filter((n) => n.status === 'done').length
  const totalCount = nodes.length
  const progress = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <Activity className="h-3 w-3 text-primary-400" />
          DAG 流水线 · Round {currentRound}
        </div>
        {totalRounds != null && (
          <span className="font-mono text-[9px] text-slate-600">共 {totalRounds} 轮</span>
        )}
      </div>

      {/* DAG visualization */}
      <div className="flex items-start gap-1 overflow-x-auto pb-1" style={{ scrollbarWidth: 'none' }}>
        {nodes.map((node, i) => (
          <div key={node.id} className="flex items-start">
            <DAGNodeComponent node={node} />
            {i < nodes.length - 1 && <Connector status={nodes[i + 1].status} />}
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div className="mt-3 flex items-center gap-2 border-t border-slate-800/60 pt-2 text-[10px] text-slate-500">
        <span className="whitespace-nowrap font-mono text-slate-300">
          {doneCount}/{totalCount} 步骤
        </span>
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-700/80">
          <div
            className="h-full bg-gradient-to-r from-sky-500 via-primary-500 to-accent-400 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="whitespace-nowrap font-mono tabular-nums">{progress}%</span>
      </div>

      {/* Legend */}
      <div className="mt-2 flex items-center gap-3 text-[9px] text-slate-500">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-emerald-500" /> 完成
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-sky-500 animate-pulse" /> 运行中
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-slate-700" /> 待执行
        </span>
      </div>
    </div>
  )
}
