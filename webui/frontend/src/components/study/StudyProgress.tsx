import { useEffect, useState } from 'react'
import { Pause, Play, X, ArrowRightCircle, Activity } from 'lucide-react'
import { useStudyStore } from '../../stores/study'
import { api } from '../../api/client'

interface Props {
  sessionId: string
  pollIntervalMs?: number
}

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  paused: '已暂停',
  error: '错误',
  complete: '已完成',
  cancelled: '已取消',
  budget_limited: '预算受限',
  monitoring: '监控中',
  needs_refresh: '需刷新证据',
}

const STATUS_COLORS: Record<string, string> = {
  queued: 'bg-slate-700 text-slate-200',
  running: 'bg-sky-700 text-sky-100',
  paused: 'bg-amber-700 text-amber-100',
  error: 'bg-rose-700 text-rose-100',
  complete: 'bg-emerald-700 text-emerald-100',
  cancelled: 'bg-slate-700 text-slate-300',
  budget_limited: 'bg-orange-700 text-orange-100',
  monitoring: 'bg-indigo-700 text-indigo-100',
  needs_refresh: 'bg-rose-800 text-rose-100',
}

export function StudyProgress({ sessionId, pollIntervalMs = 3000 }: Props) {
  const current = useStudyStore((s) => s.current)
  const setCurrent = useStudyStore((s) => s.setCurrent)
  const setError = useStudyStore((s) => s.setError)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)

  // Poll /study/status while the tab is mounted.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const r = await api.study.status(sessionId)
        if (!cancelled) setCurrent(r)
      } catch (err) {
        if (!cancelled) setError((err as Error).message)
      } finally {
        if (!cancelled) {
          timer = setTimeout(poll, pollIntervalMs)
        }
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [sessionId, pollIntervalMs, setCurrent, setError])

  if (!current || current.status === 'no_study') {
    return (
      <p className="text-xs text-slate-400">
        当前 session 暂无 study。
      </p>
    )
  }

  const studyId = current.study_id ?? ''
  const status = current.execution_status ?? 'unknown'
  const metrics = current.last_metrics ?? null
  const goalSnap = current.goal_snapshot ?? null

  const onAction = async (action: 'pause' | 'resume' | 'cancel') => {
    try {
      await api.study[action](studyId)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const onDirective = async () => {
    const text = directiveText.trim()
    if (!text) return
    setSubmittingDirective(true)
    try {
      await api.study.directive(studyId, text, 'webui')
      setDirectiveText('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmittingDirective(false)
    }
  }

  return (
    <div className="space-y-4 text-slate-100">
      <div className="flex items-center gap-2">
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
            STATUS_COLORS[status] ?? 'bg-slate-700 text-slate-100'
          }`}
        >
          {STATUS_LABELS[status] ?? status}
        </span>
        <span className="text-xs text-slate-400">
          Round {current.current_round ?? 0}
        </span>
      </div>

      <div className="text-xs text-slate-400 truncate">
        {studyId} · {current.objective}
      </div>

      {metrics && (
        <div className="rounded border border-slate-700 bg-slate-900 p-2">
          <div className="flex items-center gap-1 text-[10px] uppercase text-slate-500 mb-1">
            <Activity className="h-3 w-3" /> 最近一轮指标
          </div>
          <div className="grid grid-cols-3 gap-1 text-xs">
            {(['calmar', 'sharpe', 'max_dd'] as const).map((k) => (
              <div key={k} className="flex flex-col">
                <span className="text-slate-500">{k}</span>
                <span className="font-mono">
                  {metrics[k] !== undefined ? String(metrics[k]) : '—'}
                </span>
              </div>
            ))}
          </div>
          {current.last_verdict && (
            <div className="mt-1 text-[10px] text-slate-500">
              verdict: <span className="font-mono">{current.last_verdict}</span>
            </div>
          )}
        </div>
      )}

      {goalSnap && (
        <div>
          <h4 className="text-xs font-medium text-slate-400 mb-1">
            Goal 标准（{goalSnap.evidence_count} 证据 ·{' '}
            {goalSnap.progress_percent ?? 0}%）
          </h4>
          <div className="h-1.5 w-full rounded-full bg-slate-700 overflow-hidden">
            <div
              className="h-full bg-sky-500"
              style={{ width: `${goalSnap.progress_percent ?? 0}%` }}
            />
          </div>
          <ul className="mt-2 space-y-1 text-xs">
            {(goalSnap.criteria ?? []).map((c) => (
              <li
                key={c.criterion_id}
                className="flex items-center gap-2"
              >
                <span
                  className={`h-2 w-2 rounded-full ${
                    c.status === 'covered' ? 'bg-emerald-500' : 'bg-slate-500'
                  }`}
                />
                <span className="truncate">{c.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(current.metric_targets ?? []).length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-slate-400 mb-1">
            验收指标
          </h4>
          <ul className="space-y-0.5 text-xs">
            {(current.metric_targets ?? []).map((t, i) => (
              <li key={i} className="font-mono text-slate-300">
                {t.name} {t.op} {t.value}
              </li>
            ))}
          </ul>
        </div>
      )}

      {current.last_error && (
        <p className="text-xs text-rose-400 break-words">
          {current.last_error}
        </p>
      )}

      {/* Controls */}
      <div className="flex gap-2">
        {(status === 'running' || status === 'monitoring') && (
          <button
            onClick={() => onAction('pause')}
            className="inline-flex items-center gap-1 rounded bg-amber-600 px-2 py-1 text-xs hover:bg-amber-500"
          >
            <Pause className="h-3 w-3" /> 暂停
          </button>
        )}
        {(status === 'paused') && (
          <button
            onClick={() => onAction('resume')}
            className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500"
          >
            <Play className="h-3 w-3" /> 恢复
          </button>
        )}
        {(status !== 'complete' &&
          status !== 'cancelled' &&
          status !== 'error' &&
          status !== 'needs_refresh') && (
          <button
            onClick={() => onAction('cancel')}
            className="inline-flex items-center gap-1 rounded bg-rose-700 px-2 py-1 text-xs hover:bg-rose-600"
          >
            <X className="h-3 w-3" /> 取消
          </button>
        )}
      </div>

      {/* Mid-exec redirect (Phase 2) */}
      {(status === 'running' || status === 'monitoring') && (
        <div className="space-y-1">
          <label className="block text-[10px] text-slate-400">
            注入研究方向（下一轮 researcher 看到）
          </label>
          <textarea
            rows={2}
            value={directiveText}
            onChange={(e) => setDirectiveText(e.target.value)}
            placeholder="例：改成动量因子 + 减小 top_n"
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
          />
          <button
            type="button"
            onClick={onDirective}
            disabled={submittingDirective || !directiveText.trim()}
            className="inline-flex items-center gap-1 rounded bg-indigo-600 px-2 py-1 text-xs hover:bg-indigo-500 disabled:opacity-50"
          >
            <ArrowRightCircle className="h-3 w-3" /> 提交指令
          </button>
        </div>
      )}
    </div>
  )
}