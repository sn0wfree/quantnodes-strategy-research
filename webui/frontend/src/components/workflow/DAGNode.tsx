import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Clock, CheckCircle, XCircle, AlertCircle, Loader2 } from 'lucide-react'

export interface DAGNodeData {
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
  agentColor?: string
  agentName?: string
  type?: string
  [key: string]: unknown
}

const STATUS_CONFIG = {
  pending: {
    icon: Clock,
    borderColor: 'border-slate-600/70',
    textColor: 'text-slate-400',
    iconColor: 'text-slate-500',
    dotColor: 'bg-slate-500',
    label: '等待中',
  },
  running: {
    icon: Loader2,
    borderColor: 'border-primary-500/80',
    textColor: 'text-primary-300',
    iconColor: 'text-primary-400',
    dotColor: 'bg-primary-400',
    label: '运行中',
  },
  completed: {
    icon: CheckCircle,
    borderColor: 'border-emerald-500/70',
    textColor: 'text-emerald-300',
    iconColor: 'text-emerald-400',
    dotColor: 'bg-emerald-400',
    label: '已完成',
  },
  failed: {
    icon: XCircle,
    borderColor: 'border-red-500/70',
    textColor: 'text-red-300',
    iconColor: 'text-red-400',
    dotColor: 'bg-red-400',
    label: '失败',
  },
  skipped: {
    icon: AlertCircle,
    borderColor: 'border-slate-600/50',
    textColor: 'text-slate-500',
    iconColor: 'text-slate-600',
    dotColor: 'bg-slate-600',
    label: '已跳过',
  },
}

export const DAGNode = memo(function DAGNode({ data, selected }: NodeProps) {
  const nodeData = data as unknown as DAGNodeData
  const config = STATUS_CONFIG[nodeData.status || 'pending']
  const Icon = config.icon
  const isRunning = nodeData.status === 'running'

  return (
    <div
      className={`relative rounded-xl border-2 ${config.borderColor} px-4 py-3 min-w-[150px] transition-all dag-node-card ${
        selected ? 'dag-node-selected' : ''
      } ${isRunning ? 'animate-stage-pulse' : ''}`}
    >
      {/* Left color bar (agent color) */}
      {nodeData.agentColor && (
        <div
          className="absolute left-0 top-2 bottom-2 w-1 rounded-full"
          style={{ backgroundColor: nodeData.agentColor, boxShadow: `0 0 8px ${nodeData.agentColor}66` }}
        />
      )}

      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <span className={`flex h-4 w-4 items-center justify-center rounded ${config.dotColor}/15`}>
          <Icon
            className={`h-3.5 w-3.5 ${config.iconColor} ${isRunning ? 'animate-spin' : ''}`}
          />
        </span>
        <span className={`text-[10px] font-medium tracking-wide uppercase ${config.textColor}`}>
          {config.label}
        </span>
      </div>

      {/* Label */}
      <div className="text-sm font-medium text-slate-100 truncate">
        {nodeData.label}
      </div>

      {/* Type + agent name */}
      {nodeData.agentName && (
        <div className="mt-1.5 flex items-center gap-1">
          {nodeData.type && (
            <span className="rounded bg-slate-800/80 px-1 py-px text-[9px] font-mono text-slate-400">
              {nodeData.type}
            </span>
          )}
          <span className="truncate text-[10px] text-slate-500">{nodeData.agentName}</span>
        </div>
      )}

      {/* Handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !bg-slate-600 !border-2 !border-slate-800"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !bg-slate-600 !border-2 !border-slate-800"
      />
    </div>
  )
})
