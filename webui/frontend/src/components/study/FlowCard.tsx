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

const STATUS_CONFIG: Record<NodeStatus, { bg: string; border: string; text: string; icon: string }> = {
  done:    { bg: 'bg-emerald-500', border: 'border-emerald-500', text: 'text-emerald-400', icon: '●' },
  running: { bg: 'bg-sky-500',    border: 'border-sky-500',    text: 'text-sky-400',    icon: '◐' },
  pending: { bg: 'bg-slate-600',  border: 'border-slate-600',  text: 'text-slate-500',  icon: '○' },
}

function FlowNode({ node, isLast }: { node: FlowNodeData; isLast: boolean }) {
  const config = STATUS_CONFIG[node.status]
  const duration = node.duration_ms != null
    ? node.duration_ms >= 1000
      ? `${(node.duration_ms / 1000).toFixed(1)}s`
      : `${node.duration_ms}ms`
    : null

  return (
    <div className="flex items-start gap-2">
      {/* Node indicator */}
      <div className="flex flex-col items-center">
        <div
          className={`flex h-5 w-5 items-center justify-center rounded-full border-2 text-[10px] font-bold ${config.border} ${
            node.status === 'running' ? 'animate-pulse' : ''
          }`}
          style={node.status === 'running' ? { background: 'rgba(14, 165, 233, 0.2)' } : undefined}
        >
          {node.status === 'done' ? '✓' : node.status === 'running' ? '◉' : '○'}
        </div>
        {!isLast && (
          <div
            className={`w-0.5 h-6 ${
              node.status === 'done' ? 'bg-slate-500' : 'border-l border-dashed border-slate-700'
            }`}
          />
        )}
      </div>

      {/* Node content */}
      <div className="flex-1 min-w-0 pb-4">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-medium ${config.text}`}>{node.label}</span>
          {node.status === 'done' && duration && (
            <span className="text-[10px] text-slate-500">{duration}</span>
          )}
          {node.status === 'running' && (
            <span className="text-[10px] text-sky-400 animate-pulse">...</span>
          )}
        </div>
      </div>
    </div>
  )
}

export function FlowCard({ nodes, currentRound }: Props) {
  // Find current running node index
  const runningIdx = nodes.findIndex((n) => n.status === 'running')
  const lastDoneIdx = nodes.findLastIndex((n) => n.status === 'done')

  // Show 3 nodes: previous done, current running, next pending
  let displayNodes: FlowNodeData[]
  if (runningIdx >= 0) {
    // Has running node: show last done + running + next pending
    const prev = lastDoneIdx >= 0 && lastDoneIdx !== runningIdx ? nodes[lastDoneIdx] : null
    const current = nodes[runningIdx]
    const next = nodes.slice(runningIdx + 1).find((n) => n.status === 'pending')
    displayNodes = [prev, current, next].filter(Boolean) as FlowNodeData[]
  } else if (lastDoneIdx >= 0) {
    // All done or last done: show last 2 done + "Round 完成"
    displayNodes = nodes.slice(-2)
  } else {
    // All pending: show first 3
    displayNodes = nodes.slice(0, 3)
  }

  // Progress calculation
  const doneCount = nodes.filter((n) => n.status === 'done').length
  const totalCount = nodes.length
  const progress = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0

  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-2">
      <div className="flex items-center gap-1 text-[10px] uppercase text-slate-500 mb-2">
        <Activity className="h-3 w-3" />
        当前流程 · Round {currentRound}
      </div>

      <div className="space-y-0">
        {displayNodes.map((node, i) => (
          <FlowNode key={node.id} node={node} isLast={i === displayNodes.length - 1} />
        ))}
      </div>

      {/* Progress bar */}
      <div className="flex items-center gap-2 text-[10px] text-slate-500">
        <span>{doneCount}/{totalCount} 步骤</span>
        <div className="flex-1 h-1 rounded-full bg-slate-700 overflow-hidden">
          <div
            className="h-full bg-sky-500 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span>{progress}%</span>
      </div>
    </div>
  )
}

export type { FlowNodeData, NodeStatus }
