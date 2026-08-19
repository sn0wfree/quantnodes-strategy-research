/**
 * EventTimeline — shows recent SSE events as a compact feed.
 * Stub: will be wired to SSE store in Phase D.
 */
import type { WidgetProps } from '../types'

export function EventTimeline(_props: WidgetProps) {
  return (
    <div className="space-y-1 text-xs text-slate-500">
      <div className="text-slate-400 font-medium mb-2">最近活动</div>
      <div className="italic">事件时间线将在 SSE 接通后显示</div>
    </div>
  )
}
