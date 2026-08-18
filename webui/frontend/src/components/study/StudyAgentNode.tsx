interface StudyAgentNodeProps {
  agent: { id: string; label: string; abbr: string }
  status?: { status: string; duration_s?: number; output_summary?: string }
  onClick: () => void
}

const STATUS_CONFIG: Record<string, { border: string; bg: string; text: string; icon: string }> = {
  pending: { border: 'border-slate-700', bg: 'bg-slate-800/40', text: 'text-slate-500', icon: '·' },
  running: { border: 'border-sky-500', bg: 'bg-sky-500/10', text: 'text-sky-400', icon: '◉' },
  done: { border: 'border-emerald-500', bg: 'bg-emerald-500/10', text: 'text-emerald-400', icon: '✓' },
  error: { border: 'border-rose-500', bg: 'bg-rose-500/10', text: 'text-rose-400', icon: '✗' },
}

export function StudyAgentNode({ agent, status, onClick }: StudyAgentNodeProps) {
  const config = STATUS_CONFIG[status?.status ?? 'pending']

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-36 flex-col rounded-xl border p-3 transition-all hover:scale-[1.02] hover:shadow-lg ${config.border} ${config.bg} ${status?.status === 'running' ? 'animate-pulse' : ''}`}
      style={status?.status === 'running' ? { boxShadow: '0 0 12px rgba(14, 165, 233, 0.3)' } : undefined}
    >
      {/* Status + Duration */}
      <div className="flex items-center justify-between mb-1">
        <span className={`text-lg font-bold ${config.text}`}>{config.icon}</span>
        {status?.duration_s != null && (
          <span className="font-mono text-[10px] text-slate-500">{status.duration_s.toFixed(0)}s</span>
        )}
      </div>

      {/* Agent name */}
      <div className="text-xs font-medium text-slate-200">{agent.abbr}</div>

      {/* Output summary (truncated) */}
      {status?.output_summary && (
        <div className="mt-1 text-[10px] text-slate-400 line-clamp-2" title={status.output_summary}>
          {status.output_summary}
        </div>
      )}
    </button>
  )
}
