import { Target, CheckCircle2, Circle } from 'lucide-react'

interface Criteria {
  criterion_id: string
  text: string
  status: string
  required: boolean
}

interface Props {
  objective: string
  progressPercent?: number
  evidenceCount?: number
  criteria?: Criteria[]
}

export function ObjectiveProgress({
  objective,
  progressPercent = 0,
  evidenceCount = 0,
  criteria = [],
}: Props) {
  const coveredCount = criteria.filter((c) => c.status === 'covered').length

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <Target className="h-3 w-3 text-primary-400" />
          目标 · 进度
        </div>
        {criteria.length > 0 && (
          <span className="rounded-full border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 font-mono text-[9px] text-slate-400">
            {coveredCount}/{criteria.length} 达成
          </span>
        )}
      </div>

      <p className="mb-2.5 text-xs leading-relaxed text-slate-200 line-clamp-2">{objective}</p>

      {/* Progress bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full bg-slate-700/80 overflow-hidden">
          <div
            className="h-full bg-sky-500 bg-gradient-to-r from-sky-500 via-primary-500 to-accent-400 transition-all duration-500"
            style={{ width: `${Math.min(100, Math.max(0, progressPercent))}%` }}
          />
        </div>
        <span className="whitespace-nowrap font-mono text-[10px] tabular-nums text-slate-400">
          {progressPercent}% ({evidenceCount} 证据)
        </span>
      </div>

      {/* Criteria list */}
      {criteria.length > 0 && (
        <div className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1">
          {criteria.map((c) => {
            const covered = c.status === 'covered'
            return (
              <div
                key={c.criterion_id}
                className="criteria-item flex items-center gap-1.5 text-[10px]"
              >
                {covered ? (
                  <CheckCircle2 className="h-3 w-3 flex-shrink-0 text-emerald-500" />
                ) : (
                  <Circle className="h-3 w-3 flex-shrink-0 text-slate-600" />
                )}
                <span
                  className={`truncate ${covered ? 'text-slate-300' : 'text-slate-500'}`}
                  title={c.text}
                >
                  {c.text}
                </span>
                {c.required && (
                  <span className="flex-shrink-0 text-[9px] text-amber-500/80">必</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
