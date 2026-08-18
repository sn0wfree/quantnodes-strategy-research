import { ChevronLeft, ChevronRight } from 'lucide-react'

interface RoundPickerProps {
  currentRound: number
  totalRounds?: number
  onChange: (round: number) => void
}

export function RoundPicker({ currentRound, totalRounds, onChange }: RoundPickerProps) {
  const min = 1
  const canPrev = currentRound > min
  const canNext = true // user can navigate forward to future rounds (which will be empty)

  return (
    <div className="flex items-center gap-1 text-[10px] text-slate-400">
      <button
        type="button"
        onClick={() => canPrev && onChange(currentRound - 1)}
        disabled={!canPrev}
        className="rounded p-1 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
        aria-label="上一轮"
      >
        <ChevronLeft className="h-3 w-3" />
      </button>
      <span className="rounded bg-slate-800/70 px-2 py-0.5 font-mono tabular-nums">
        Round {currentRound}
        {totalRounds != null && ` / ${totalRounds}`}
      </span>
      <button
        type="button"
        onClick={() => canNext && onChange(currentRound + 1)}
        disabled={!canNext}
        className="rounded p-1 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-30 disabled:cursor-not-allowed"
        aria-label="下一轮"
      >
        <ChevronRight className="h-3 w-3" />
      </button>
    </div>
  )
}