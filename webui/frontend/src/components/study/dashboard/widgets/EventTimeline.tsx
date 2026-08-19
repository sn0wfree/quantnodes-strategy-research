/**
 * EventTimeline — shows recent SSE events as a compact feed.
 * Reads from useStudyStore.recentEvents (SSE-driven).
 */
import { useStudyStore } from '../../../../stores/study'

function formatTime(ts: number): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return '--:--:--'
  }
}

const TYPE_ICONS: Record<string, { icon: string; color: string }> = {
  phase: { icon: '📍', color: 'text-sky-400' },
  agent: { icon: '🤖', color: 'text-violet-400' },
  knowledge: { icon: '📚', color: 'text-emerald-400' },
  review: { icon: '🔍', color: 'text-amber-400' },
  retry: { icon: '🔄', color: 'text-orange-400' },
  evidence: { icon: '✓', color: 'text-emerald-400' },
  directive: { icon: '💬', color: 'text-indigo-400' },
  other: { icon: '•', color: 'text-slate-400' },
}

export function EventTimeline() {
  const events = useStudyStore((st) => st.recentEvents)

  if (events.length === 0) {
    return (
      <div className="space-y-1 text-xs text-slate-500">
        <div className="text-slate-400 font-medium mb-2">最近活动</div>
        <div className="italic">暂无事件 — 等待 SSE 数据...</div>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      <div className="text-xs text-slate-400 font-medium mb-2">最近活动</div>
      <div className="max-h-48 overflow-y-auto space-y-1">
        {events.map((event, i) => {
          const { icon, color } = TYPE_ICONS[event.type] ?? TYPE_ICONS.other
          return (
            <div
              key={`${event.timestamp}-${i}`}
              className="flex items-start gap-2 text-xs"
            >
              <span className="font-mono text-[10px] text-slate-600 w-16 flex-shrink-0">
                {formatTime(event.timestamp)}
              </span>
              <span className={`flex-shrink-0 ${color}`}>{icon}</span>
              <span className="text-slate-300 min-w-0">
                {event.message}
                {event.round != null && (
                  <span className="ml-1 text-slate-600">R{event.round}</span>
                )}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
