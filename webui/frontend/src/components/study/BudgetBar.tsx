interface Props {
  usedTurns?: number
  totalTurns?: number | null
  usedTimeS?: number
  totalTimes?: number | null
}

export function BudgetBar({ usedTurns, totalTurns, usedTimeS, totalTimes }: Props) {
  const hasTurnBudget = totalTurns != null && totalTurns > 0
  const hasTimeBudget = totalTimes != null && totalTimes > 0

  if (!hasTurnBudget && !hasTimeBudget) return null

  const turnPercent = hasTurnBudget ? Math.min(100, ((usedTurns ?? 0) / totalTurns!) * 100) : 0
  const timePercent = hasTimeBudget ? Math.min(100, ((usedTimeS ?? 0) / totalTimes!) * 100) : 0

  const formatTime = (s: number) => {
    if (s < 60) return `${Math.round(s)}s`
    if (s < 3600) return `${Math.round(s / 60)}m`
    return `${(s / 3600).toFixed(1)}h`
  }

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
        预算使用
      </div>
      {hasTurnBudget && (
        <div className="space-y-0.5">
          <div className="flex justify-between text-[10px] text-slate-400">
            <span>轮数</span>
            <span>{usedTurns ?? 0} / {totalTurns}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                turnPercent > 90 ? 'bg-rose-500' : turnPercent > 70 ? 'bg-amber-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${turnPercent}%` }}
            />
          </div>
        </div>
      )}
      {hasTimeBudget && (
        <div className="space-y-0.5">
          <div className="flex justify-between text-[10px] text-slate-400">
            <span>时间</span>
            <span>{formatTime(usedTimeS ?? 0)} / {formatTime(totalTimes!)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                timePercent > 90 ? 'bg-rose-500' : timePercent > 70 ? 'bg-amber-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${timePercent}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
