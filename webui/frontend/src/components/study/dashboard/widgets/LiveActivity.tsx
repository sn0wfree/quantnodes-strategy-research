/**
 * LiveActivity — shows real-time phase, agent, and elapsed time.
 * Stub: will be wired to SSE store in Phase D.
 */
import type { WidgetProps } from '../types'

export function LiveActivity({ summary }: WidgetProps) {
  const status = summary.execution_status
  const round = summary.current_round

  return (
    <div className="flex items-center gap-6 text-sm">
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${
          status === 'running' ? 'bg-emerald-400 animate-pulse' :
          status === 'queued' ? 'bg-amber-400' :
          'bg-slate-500'
        }`} />
        <span className="text-slate-300">
          {status === 'running' ? '运行中' :
           status === 'queued' ? '排队中' :
           status === 'complete' ? '已完成' :
           status === 'paused' ? '已暂停' :
           status === 'cancelled' ? '已取消' :
           status === 'error' ? '错误' :
           status}
        </span>
      </div>
      <div className="text-slate-400">
        Round <span className="text-slate-200 font-medium">{round}</span>
        {summary.max_rounds && ` / ${summary.max_rounds}`}
      </div>
      {/* Placeholder for phase / agent / elapsed — will be wired via SSE */}
      <div className="text-xs text-slate-500">
        ⚡ 实时数据将在 SSE 接通后显示
      </div>
    </div>
  )
}
