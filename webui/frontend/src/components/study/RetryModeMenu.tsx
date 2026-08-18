import { RotateCcw } from 'lucide-react'

interface RetryModeMenuProps {
  onSelect: (mode: 'append' | 'restart') => void
}

export function RetryModeMenu({ onSelect }: RetryModeMenuProps) {
  return (
    <div className="absolute right-0 top-full z-30 mt-1 w-72 rounded-lg border border-slate-700 bg-slate-900/95 p-3 shadow-elevated backdrop-blur">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <RotateCcw className="h-3 w-3" />
        重试模式
      </div>

      {/* append mode (default) */}
      <button
        type="button"
        onClick={() => onSelect('append')}
        className="group flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-lg border border-emerald-600/30 bg-emerald-900/15 px-3 py-2 text-left transition-colors hover:bg-emerald-900/30"
      >
        <div className="flex w-full items-center justify-between">
          <span className="text-[11px] font-medium text-emerald-200">
            从下一轮继续（推荐）
          </span>
          <RotateCcw className="h-3.5 w-3.5 text-emerald-400" />
        </div>
        <span className="text-[9px] text-slate-400">
          Round N → N+1，保留所有历史轮次
        </span>
      </button>

      {/* restart mode */}
      <button
        type="button"
        onClick={() => onSelect('restart')}
        className="mt-2 group flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-lg border border-amber-600/30 bg-amber-900/15 px-3 py-2 text-left transition-colors hover:bg-amber-900/30"
      >
        <div className="flex w-full items-center justify-between">
          <span className="text-[11px] font-medium text-amber-200">
            从第 1 轮重试
          </span>
          <RotateCcw className="h-3.5 w-3.5 text-amber-400" />
        </div>
        <span className="text-[9px] text-slate-400">
          清空所有旧 round，从头开始
        </span>
      </button>
    </div>
  )
}