import { Target } from 'lucide-react'

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
  return (
    <div className="rounded border border-slate-700 bg-slate-900 p-2">
      <div className="flex items-center gap-1 text-[10px] uppercase text-slate-500 mb-2">
        <Target className="h-3 w-3" />
        目标 · 进度
      </div>

      <p className="text-xs text-slate-200 mb-2 line-clamp-2">{objective}</p>

      {/* Progress bar */}
      <div className="flex items-center gap-2 mb-2">
        <div className="flex-1 h-1.5 rounded-full bg-slate-700 overflow-hidden">
          <div
            className="h-full bg-sky-500 transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <span className="text-[10px] text-slate-400 whitespace-nowrap">
          {progressPercent}% ({evidenceCount} 证据)
        </span>
      </div>

      {/* Criteria list */}
      {criteria.length > 0 && (
        <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
          {criteria.map((c) => (
            <div key={c.criterion_id} className="flex items-center gap-1.5 text-[10px]">
              <span
                className={`h-1.5 w-1.5 rounded-full flex-shrink-0 ${
                  c.status === 'covered' ? 'bg-emerald-500' : 'bg-slate-600'
                }`}
              />
              <span className={`truncate ${c.status === 'covered' ? 'text-slate-300' : 'text-slate-500'}`}>
                {c.text}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
