import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, Send, Clock, FolderOpen, User,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyDirectivesResponse } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { ObjectiveProgress } from './ObjectiveProgress'
import { RoundHistory } from './RoundHistory'
import { ScoreboardMini } from './ScoreboardMini'
import { EmptyState } from '../common/EmptyState'

function formatDateTime(iso?: string): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

export function StudyDetailPage() {
  const { studyId = '' } = useParams<{ studyId: string }>()
  const navigate = useNavigate()
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [directives, setDirectives] = useState<StudyDirectivesResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)

  const loadDirectives = useCallback(async () => {
    try {
      const r = await api.study.directives(studyId)
      setDirectives(r)
    } catch {
      // Non-critical — audit trail can be absent
    }
  }, [studyId])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      try {
        const r = await api.study.summary(studyId)
        if (cancelled) return
        setSummary(r)
        setNotFound(false)
        setError('')
      } catch (err) {
        if (cancelled) return
        const status = (err as { status?: number })?.status
        if (status === 404) {
          setNotFound(true)
        } else {
          setError((err as Error).message)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
          timer = setTimeout(poll, 5000)
        }
      }
    }

    void poll()
    void loadDirectives()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [studyId, loadDirectives])

  const onAction = async (action: 'pause' | 'resume' | 'cancel') => {
    setBusy(true)
    try {
      await api.study[action](studyId)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onDirective = async () => {
    const text = directiveText.trim()
    if (!text) return
    setSubmittingDirective(true)
    try {
      await api.study.directive(studyId, text, 'webui')
      setDirectiveText('')
      await loadDirectives()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmittingDirective(false)
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-sky-500" />
          加载中...
        </div>
      </div>
    )
  }

  if (notFound || !summary) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-950">
        <EmptyState
          icon={<FolderOpen className="h-10 w-10" />}
          title="研究任务不存在"
          description="该 study 可能已被删除，或链接不正确。"
        />
        <Link to="/" className="text-sm text-sky-400 hover:text-sky-300 hover:underline">
          返回聊天
        </Link>
      </div>
    )
  }

  const status = summary.execution_status ?? 'unknown'
  const strategyName = summary.strategy_name ?? ''
  const workspacePath = summary.workspace_path ?? ''

  const openRun = (runName: string) => {
    if (!strategyName) return
    navigate(
      `/run/${encodeURIComponent(strategyName)}/${encodeURIComponent(runName)}`
    )
  }

  const canPause = status === 'running' || status === 'monitoring'
  const canResume = status === 'paused' || status === 'interrupted'
  const canCancel =
    status !== 'complete' && status !== 'cancelled' &&
    status !== 'error' && status !== 'needs_refresh' &&
    status !== 'interrupted'

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Top bar */}
      <header className="flex items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-4 py-2.5">
        <button
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> 返回
        </button>
        <h1 className="flex-1 truncate text-sm font-medium text-slate-200">
          {summary.objective || '研究详情'}
        </h1>
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${STUDY_STATUS_COLORS[status] ?? 'bg-slate-700 text-slate-100'}`}>
          {STUDY_STATUS_LABELS[status] ?? status}
        </span>
        <span className="text-xs text-slate-400">
          Round {summary.current_round ?? 0}/{summary.max_rounds ?? 5}
        </span>
        <div className="flex items-center gap-1.5">
          {canPause && (
            <button
              onClick={() => onAction('pause')}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded bg-amber-600 px-2 py-1 text-xs hover:bg-amber-500 disabled:opacity-50"
            >
              <Pause className="h-3 w-3" /> 暂停
            </button>
          )}
          {canResume && (
            <button
              onClick={() => onAction('resume')}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500 disabled:opacity-50"
            >
              <Play className="h-3 w-3" /> 恢复
            </button>
          )}
          {canCancel && (
            <button
              onClick={() => onAction('cancel')}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded bg-rose-700 px-2 py-1 text-xs hover:bg-rose-600 disabled:opacity-50"
            >
              <X className="h-3 w-3" /> 取消
            </button>
          )}
        </div>
      </header>

      {/* Meta strip */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-800/60 bg-slate-950 px-4 py-1.5 text-[10px] text-slate-500">
        <span className="inline-flex items-center gap-1">
          <FolderOpen className="h-3 w-3" /> 策略: {strategyName || '—'}
        </span>
        <span className="inline-flex items-center gap-1 truncate" title={workspacePath}>
          <User className="h-3 w-3" /> {workspacePath || '—'}
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" /> 创建: {formatDateTime(summary.created_at)}
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" /> 更新: {formatDateTime(summary.updated_at)}
        </span>
      </div>

      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* Body */}
      <main className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-3">
        <div className="space-y-3 xl:col-span-2">
          <ObjectiveProgress
            objective={summary.objective}
            progressPercent={summary.goal_snapshot?.progress_percent ?? 0}
            evidenceCount={summary.goal_snapshot?.evidence_count ?? 0}
            criteria={summary.goal_snapshot?.criteria ?? []}
          />
          <RoundHistory
            rounds={summary.recent_rounds ?? []}
            currentRound={summary.current_round ?? 1}
            onOpenRun={openRun}
          />
          <ScoreboardMini scoreboard={summary.scoreboard ?? []} />
        </div>

        <div className="space-y-3">
          {/* Directive input */}
          {(canPause || canResume) && (
            <div className="rounded border border-slate-700 bg-slate-900 p-2 space-y-1">
              <label className="block text-[10px] text-slate-400">
                注入研究方向（下一轮 researcher 看到）
              </label>
              <textarea
                rows={2}
                value={directiveText}
                onChange={(e) => setDirectiveText(e.target.value)}
                placeholder="例：改成动量因子 + 减小 top_n"
                className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
              />
              <button
                type="button"
                onClick={onDirective}
                disabled={submittingDirective || !directiveText.trim()}
                className="inline-flex items-center gap-1 rounded bg-indigo-600 px-2 py-1 text-xs hover:bg-indigo-500 disabled:opacity-50"
              >
                <Send className="h-3 w-3" /> 提交指令
              </button>
            </div>
          )}

          {/* Directives audit trail */}
          <div className="rounded border border-slate-700 bg-slate-900 p-2">
            <div className="mb-2 flex items-center gap-1 text-[10px] uppercase text-slate-500">
              <Clock className="h-3 w-3" /> 指令记录
            </div>
            {(directives?.directives?.length ?? 0) === 0 ? (
              <p className="text-xs text-slate-500">暂无指令</p>
            ) : (
              <ul className="space-y-1.5">
                {directives!.directives.map((d) => (
                  <li key={d.directive_id} className="rounded bg-slate-950/60 p-1.5 text-[11px]">
                    <p className="text-slate-300">{d.content}</p>
                    <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-500">
                      <span>{formatDateTime(d.created_at)}</span>
                      {d.issued_by && <span>· {d.issued_by}</span>}
                      <span className={d.consumed_at ? 'text-emerald-500' : 'text-amber-500'}>
                        {d.consumed_at ? '已消费' : '待消费'}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
