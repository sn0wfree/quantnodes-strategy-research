import { BarChart3 } from 'lucide-react'
import type { LeverScoreSummary } from '../../api/client'

interface Props {
  scoreboard: LeverScoreSummary[]
}

function PrecisionBar({ precision }: { precision: number }) {
  const width = Math.min(100, Math.max(0, Math.round(precision * 100)))
  const color =
    precision >= 0.7 ? 'bg-emerald-500' :
    precision >= 0.5 ? 'bg-amber-500' :
    'bg-rose-500'

  return (
    <div className="flex-1 h-1.5 rounded-full bg-slate-700/80 overflow-hidden">
      <div
        className={`h-full ${color} transition-all duration-500`}
        style={{ width: `${width}%` }}
      />
    </div>
  )
}

export function ScoreboardMini({ scoreboard }: Props) {
  if (scoreboard.length === 0) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
        <div className="mb-1 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <BarChart3 className="h-3 w-3" />
          Scoreboard
        </div>
        <p className="text-xs text-slate-500">暂无数据</p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <BarChart3 className="h-3 w-3 text-primary-400" />
        Scoreboard
      </div>

      <div className="space-y-1.5">
        {scoreboard.map((item) => (
          <div key={item.lever} className="flex items-center gap-2">
            <span className="w-16 truncate text-[10px] text-slate-400" title={item.lever}>
              {item.lever}
            </span>
            <PrecisionBar precision={item.precision_mean} />
            <span className="w-8 text-right font-mono text-[10px] tabular-nums text-slate-300">
              {item.precision_mean.toFixed(2)}
            </span>
            <span className="w-10 text-right text-[10px] text-slate-500">
              {item.accepted}/{item.attempts}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
