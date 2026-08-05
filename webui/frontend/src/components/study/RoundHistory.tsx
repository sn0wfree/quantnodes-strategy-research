import { useState } from 'react'
import { ChevronRight, ChevronDown, AlertTriangle, ExternalLink } from 'lucide-react'
import type { StudyRoundSummary } from '../../api/client'

interface Props {
  rounds: StudyRoundSummary[]
  currentRound: number
  /** When provided, renders a per-round link to the run detail page. */
  onOpenRun?: (runName: string) => void
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  } catch {
    return '--:--'
  }
}

function MetricValue({ label, value }: { label: string; value?: number | null }) {
  const display = value != null ? value.toFixed(2) : '—'
  return (
    <span className="font-mono text-[10px]">
      <span className="text-slate-500">{label}:</span>{' '}
      <span className="text-slate-300">{display}</span>
    </span>
  )
}

function RoundItem({
  round,
  isCurrent,
  onOpenRun,
}: {
  round: StudyRoundSummary
  isCurrent: boolean
  onOpenRun?: (runName: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const hasFailures = round.factor_failures && round.factor_failures.length > 0

  return (
    <div>
      <div className="flex items-center gap-1 rounded hover:bg-slate-800 transition-colors">
        <button
          onClick={() => setExpanded(!expanded)}
          className={`flex flex-1 items-center gap-1.5 text-left py-1 px-1 rounded transition-colors ${
            isCurrent ? 'bg-slate-800/50' : ''
          }`}
        >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-slate-500 flex-shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 text-slate-500 flex-shrink-0" />
        )}
        <span className="text-[10px] text-slate-400 w-6">R{round.round_num}</span>
        <span className="text-[10px] text-slate-500 w-10">{formatTime(round.created_at)}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-slate-400">
          {round.run_name}
        </span>
        <span
          className={`text-[10px] px-1 rounded ${
            round.verdict === 'keep'
              ? 'bg-emerald-900/50 text-emerald-400'
              : 'bg-slate-800 text-slate-400'
          }`}
        >
          {round.verdict ?? '—'}
          {round.verdict === 'keep' && ' ✓'}
        </span>
        <MetricValue label="C" value={round.metrics?.calmar} />
        <MetricValue label="S" value={round.metrics?.sharpe} />
        <MetricValue label="D" value={round.metrics?.max_dd} />
        {hasFailures && (
          <AlertTriangle className="h-3 w-3 text-amber-500 flex-shrink-0 ml-auto" />
        )}
        </button>
        {onOpenRun && (
          <button
            type="button"
            title="查看回测产物"
            onClick={() => onOpenRun(round.run_name)}
            className="p-1 rounded text-slate-500 hover:text-sky-400 hover:bg-slate-800 transition-colors"
          >
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="ml-5 mr-1 mb-2 border-l-2 border-slate-700 pl-2 space-y-2">
          {/* Factor failures */}
          {hasFailures && (
            <div className="space-y-1">
              {round.factor_failures!.map((f, i) => (
                <div key={i} className="text-[10px]">
                  <div className="flex items-center gap-1 text-amber-400">
                    <AlertTriangle className="h-3 w-3" />
                    <span className="font-mono">{f.factor_code}</span>
                  </div>
                  <p className="text-slate-500 ml-4">{f.error}</p>
                  {f.available_columns && (
                    <p className="text-slate-600 ml-4">
                      可用列: [{f.available_columns.join(', ')}]
                    </p>
                  )}
                  {f.suggested_fix && (
                    <p className="text-sky-400 ml-4">建议: {f.suggested_fix}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function RoundHistory({ rounds, currentRound, onOpenRun }: Props) {
  if (rounds.length === 0) {
    return (
      <div className="rounded border border-slate-700 bg-slate-900 p-2">
        <div className="text-[10px] uppercase text-slate-500 mb-1">Round 历史</div>
        <p className="text-xs text-slate-500">暂无历史记录</p>
      </div>
    )
  }

  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-2">
      <div className="text-[10px] uppercase text-slate-500 mb-1">Round 历史</div>
      <div className="space-y-0">
        {rounds.map((round) => (
          <RoundItem
            key={round.round_num}
            round={round}
            isCurrent={round.round_num === currentRound}
            onOpenRun={onOpenRun}
          />
        ))}
      </div>
    </div>
  )
}
