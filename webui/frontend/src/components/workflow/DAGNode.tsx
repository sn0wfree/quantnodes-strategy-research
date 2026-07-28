import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Clock, CheckCircle, XCircle, AlertCircle, Zap, Loader2 } from 'lucide-react'

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
    borderColor: 'border-slate-500',
    bgColor: 'bg-slate-800/50',
    textColor: 'text-slate-400',
    iconColor: 'text-slate-500',
    label: '等待中',
  },
  running: {
    icon: Loader2,
    borderColor: 'border-blue-500',
    bgColor: 'bg-blue-950/30',
    textColor: 'text-blue-300',
    iconColor: 'text-blue-400',
    label: '运行中',
  },
  completed: {
    icon: CheckCircle,
    borderColor: 'border-emerald-500',
    bgColor: 'bg-emerald-950/30',
    textColor: 'text-emerald-300',
    iconColor: 'text-emerald-400',
    label: '已完成',
  },
  failed: {
    icon: XCircle,
    borderColor: 'border-red-500',
    bgColor: 'bg-red-950/30',
    textColor: 'text-red-300',
    iconColor: 'text-red-400',
    label: '失败',
  },
  skipped: {
    icon: AlertCircle,
    borderColor: 'border-slate-600',
    bgColor: 'bg-slate-800/30',
    textColor: 'text-slate-500',
    iconColor: 'text-slate-600',
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
      className={`relative rounded-lg border-2 ${config.borderColor} ${config.bgColor} px-4 py-3 min-w-[140px] transition-all ${
        selected ? 'ring-2 ring-primary-500/50' : ''
      }`}
    >
      {/* Left color bar (agent color) */}
      {nodeData.agentColor && (
        <div
          className="absolute left-0 top-0 bottom-0 w-1 rounded-l-lg"
          style={{ backgroundColor: nodeData.agentColor }}
        />
      )}

      {/* Header */}
      <div className="flex items-center gap-2 mb-1">
        <Icon
          className={`h-4 w-4 ${config.iconColor} ${isRunning ? 'animate-spin' : ''}`}
        />
        <span className={`text-xs font-medium ${config.textColor}`}>
          {config.label}
        </span>
      </div>

      {/* Label */}
      <div className="text-sm font-medium text-slate-100 truncate">
        {nodeData.label}
      </div>

      {/* Agent name */}
      {nodeData.agentName && (
        <div className="text-[10px] text-slate-500 mt-1">
          {nodeData.agentName}
        </div>
      )}

      {/* Handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !bg-slate-500 !border-2 !border-slate-700"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !bg-slate-500 !border-2 !border-slate-700"
      />
    </div>
  )
})
