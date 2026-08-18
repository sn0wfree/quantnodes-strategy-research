import { useEffect, useState } from 'react'
import { History, Loader2, X } from 'lucide-react'
import { api, type StudyObjectiveHistoryEntry } from '../../api/client'
import { formatDateTime } from './utils'

interface StudyObjectiveHistoryProps {
  studyId: string
  open: boolean
  onClose: () => void
}

export function StudyObjectiveHistory({
  studyId,
  open,
  onClose,
}: StudyObjectiveHistoryProps) {
  const [history, setHistory] = useState<StudyObjectiveHistoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const run = async () => {
      setLoading(true)
      setError('')
      try {
        const r = await api.study.objectiveHistory(studyId)
        if (!cancelled) setHistory(r.history)
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message)
          setHistory([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [open, studyId, reloadKey])

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="目标变更历史"
      className="fixed inset-0 z-40 flex justify-end bg-slate-950/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-md flex-col gap-3 overflow-y-auto border-l border-slate-700 bg-slate-900 p-5 shadow-elevated"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <History className="h-4 w-4 text-primary-400" />
            目标变更历史
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="text-[10px] text-slate-500">
          所有目标修改记录按时间倒序展示。「等待下一轮生效」表示该替换尚未被任何轮次使用。
        </p>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
          </div>
        ) : error ? (
          <div className="rounded-lg border border-rose-800 bg-rose-950/50 px-3 py-2 text-[11px] text-rose-300">
            {error}
          </div>
        ) : history.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-800 px-4 py-12 text-center text-xs text-slate-600">
            暂无目标变更记录
          </div>
        ) : (
          <ol className="relative space-y-2 border-l-2 border-slate-800 pl-4">
            {history.map((entry) => {
              const isPending = entry.applied_round == null
              return (
                <li
                  key={entry.id}
                  className="relative rounded-lg border border-slate-800 bg-slate-950/40 p-3"
                >
                  <span
                    className={`absolute -left-[1.32rem] top-4 h-3 w-3 rounded-full border-2 border-slate-900 ${
                      isPending
                        ? 'bg-amber-400 animate-pulse'
                        : 'bg-emerald-400'
                    }`}
                  />
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[10px]">
                    <span className="font-mono text-slate-500">
                      #{entry.id}
                    </span>
                    <span className="text-slate-600">
                      {formatDateTime(entry.applied_at)}
                    </span>
                    {isPending ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-300">
                        ⧖ 等待下一轮生效
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-medium text-emerald-300">
                        ✓ 第 {entry.applied_round} 轮生效
                      </span>
                    )}
                  </div>
                  <p className="line-clamp-3 text-[11px] leading-relaxed text-slate-300">
                    {entry.objective}
                  </p>
                  {entry.reason && (
                    <p className="mt-1.5 line-clamp-2 text-[10px] leading-relaxed text-slate-500">
                      💬 {entry.reason}
                    </p>
                  )}
                  {entry.replaced_by && (
                    <p className="mt-1 text-[9px] text-slate-600">
                      操作者: {entry.replaced_by}
                    </p>
                  )}
                </li>
              )
            })}
          </ol>
        )}

        <button
          type="button"
          onClick={() => setReloadKey((k) => k + 1)}
          disabled={loading}
          className="mt-auto self-start rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1 text-[10px] text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 className="inline h-3 w-3 animate-spin" />
          ) : (
            '刷新'
          )}
        </button>
      </div>
    </div>
  )
}