import { BarChart3 } from 'lucide-react'
import type { LeverScoreSummary } from '../../api/client'

interface Props {
  scoreboard: LeverScoreSummary[]
}

function PrecisionBar({ precision }: { precision: number }) {
  const width = Math.round(precision * 100)
  const color =
    precision >= 0.7 ? 'bg-emerald-500' :
    precision >= 0.5 ? 'bg-amber-500' :
    'bg-rose-500'

  return (
    <div className="flex-1 h-1.5 rounded-full bg-slate-700 overflow-hidden">
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
      <div className="rounded border border-slate-700 bg-slate-900 p-2">
        <div className="flex items-center gap-1 text-[10px] uppercase text-slate-500 mb-1">
          <BarChart3 className="h-3 w-3" />
          Scoreboard
        </div>
        <p className="text-xs text-slate-500">暂无数据</p>
      </div>
    )
  }

  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-2">
      <div className="flex items-center gap-1 text-[10px] uppercase text-slate-500 mb-2">
        <BarChart3 className="h-3 w-3" />
        Scoreboard
      </div>

      <div className="space-y-1.5">
        {scoreboard.map((item) => (
          <div key={item.lever} className="flex items-center gap-2">
            <span className="text-[10px] text-slate-400 w-16 truncate">{item.lever}</span>
            <PrecisionBar precision={item.precision_mean} />
            <span className="text-[10px] font-mono text-slate-300 w-8 text-right">
              {item.precision_mean.toFixed(2)}
            </span>
            <span className="text-[10px] text-slate-500 w-10 text-right">
              {item.accepted}/{item.attempts}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
