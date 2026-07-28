import { Clock, FileText, MessageSquare, Zap } from 'lucide-react'

interface TimelineEvent {
  id: string
  type: 'evidence' | 'iteration' | 'message' | 'system'
  content: string
  timestamp: number
  agent_id?: string
  metadata?: Record<string, unknown>
}

interface GoalTimelineProps {
  events: TimelineEvent[]
}

const EVENT_ICON = {
  evidence: <FileText className="h-3 w-3 text-emerald-400" />,
  iteration: <Zap className="h-3 w-3 text-amber-400" />,
  message: <MessageSquare className="h-3 w-3 text-blue-400" />,
  system: <Clock className="h-3 w-3 text-slate-400" />,
}

const EVENT_DOT = {
  evidence: 'bg-emerald-500',
  iteration: 'bg-amber-500',
  message: 'bg-blue-500',
  system: 'bg-slate-500',
}

function formatTimestamp(ts: number): string {
  const date = new Date(ts * 1000)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)

  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} 小时前`
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

export function GoalTimeline({ events }: GoalTimelineProps) {
  // Sort by timestamp descending (newest first)
  const sorted = [...events].sort((a, b) => b.timestamp - a.timestamp)

  if (sorted.length === 0) {
    return (
      <div className="text-xs text-slate-600 italic py-4 text-center">
        暂无活动记录
      </div>
    )
  }

  return (
    <div className="relative">
      {/* Vertical line */}
      <div className="absolute left-[11px] top-2 bottom-2 w-px bg-slate-700/50" />

      <div className="space-y-3">
        {sorted.map((event) => (
          <div key={event.id} className="relative flex gap-3">
            {/* Dot */}
            <div className={`relative z-10 mt-1 h-2.5 w-2.5 rounded-full ${EVENT_DOT[event.type]} flex-shrink-0 ring-2 ring-slate-900`} />

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 mb-0.5">
                {EVENT_ICON[event.type]}
                <span className="text-[10px] text-slate-500">
                  {formatTimestamp(event.timestamp)}
                </span>
                {event.agent_id && (
                  <span className="text-[10px] text-slate-600">
                    · {event.agent_id.slice(0, 6)}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-300 leading-relaxed line-clamp-2">
                {event.content}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
