import { Fragment } from 'react'
import { Activity } from 'lucide-react'

type NodeStatus = 'pending' | 'running' | 'done'

interface FlowNodeData {
  id: string
  label: string
  status: NodeStatus
  started_at?: string
  duration_ms?: number
}

interface Props {
  nodes: FlowNodeData[]
  currentRound: number
  totalRounds?: number
}

const STATUS_CONFIG: Record<NodeStatus, { border: string; bg: string; text: string; ring: string }> = {
  done: {
    border: 'border-emerald-500',
    bg: 'bg-emerald-500',
    text: 'text-emerald-400',
    ring: 'shadow-emerald-500/30',
  },
  running: {
    border: 'border-sky-500',
    bg: 'bg-sky-500/20',
    text: 'text-sky-400',
    ring: 'shadow-sky-500/40',
  },
  pending: {
    border: 'border-slate-700',
    bg: 'bg-slate-800/40',
    text: 'text-slate-500',
    ring: 'shadow-transparent',
  },
}

function StepperNode({ node }: { node: FlowNodeData }) {
  const config = STATUS_CONFIG[node.status]
  const isDone = node.status === 'done'
  const isRunning = node.status === 'running'

  return (
    <div className="flex w-11 flex-shrink-0 flex-col items-center gap-1">
      <div
        className={`flex h-6 w-6 items-center justify-center rounded-full border-2 text-[10px] font-bold transition-all duration-200 ${config.border} ${
          isRunning ? 'animate-pulse shadow-lg' : ''
        }`}
        style={isRunning ? { boxShadow: '0 0 12px rgba(14, 165, 233, 0.45)' } : undefined}
      >
        {isDone ? (
          <span className="text-emerald-50">✓</span>
        ) : isRunning ? (
          <span className="h-2 w-2 animate-ping rounded-full bg-sky-400" />
        ) : (
          <span className="text-slate-500">·</span>
        )}
      </div>
      <span
        className={`w-full truncate text-center text-[9px] leading-tight ${config.text}`}
        title={node.label}
      >
        {node.label}
      </span>
    </div>
  )
}

function Connector({ to }: { to: FlowNodeData }) {
  const done = to.status === 'done'
  const running = to.status === 'running'
  return (
    <div className="mt-2.5 min-w-0 flex-1">
      <div
        className={`h-0.5 rounded-full ${
          done
            ? 'bg-emerald-500/60'
            : running
              ? 'animate-pulse bg-sky-500/60'
              : 'border-t border-dashed border-slate-700'
        }`}
      />
    </div>
  )
}

export function FlowCard({ nodes, currentRound, totalRounds }: Props) {
  const doneCount = nodes.filter((n) => n.status === 'done').length
  const totalCount = nodes.length
  const progress = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500" title="节点状态按当前轮次估算">
          <Activity className="h-3 w-3 text-primary-400" />
          当前流程 · Round {currentRound}
        </div>
        {totalRounds != null && (
          <span className="font-mono text-[9px] text-slate-600">共 {totalRounds} 轮</span>
        )}
      </div>

      <div className="flex items-start gap-1 overflow-x-auto pb-1" style={{ scrollbarWidth: 'none' }}>
        {nodes.map((node, i) => (
          <Fragment key={node.id}>
            <StepperNode node={node} />
            {i < nodes.length - 1 && <Connector to={nodes[i + 1]} />}
          </Fragment>
        ))}
      </div>

      {/* Progress bar */}
      <div className="mt-2 flex items-center gap-2 border-t border-slate-800/60 pt-2 text-[10px] text-slate-500">
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
    </div>
  )
}

export type { FlowNodeData, NodeStatus }
