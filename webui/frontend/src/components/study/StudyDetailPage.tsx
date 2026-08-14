import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, Send, Clock, FolderOpen,
  Target, Activity, RotateCcw, BarChart3, BookOpen, Info,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyDirectivesResponse } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { ObjectiveProgress } from './ObjectiveProgress'
import { RoundHistory } from './RoundHistory'
import { ScoreboardMini } from './ScoreboardMini'
import { MetricsCompare } from './MetricsCompare'
import { EmptyState } from '../common/EmptyState'
import { PageShell } from '../layout/PageShell'

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

function KpiCard({
  icon,
  iconCls,
  value,
  label,
  valueCls = 'text-slate-100',
}: {
  icon: React.ReactNode
  iconCls: string
  value: string
  label: string
  valueCls?: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3.5 shadow-soft transition-colors hover:border-slate-700">
      <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg ${iconCls}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <div className={`font-mono text-xl font-bold tabular-nums ${valueCls}`}>{value}</div>
        <div className="text-[10px] text-slate-500">{label}</div>
      </div>
    </div>
  )
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
      <div className="flex min-h-screen items-center justify-center bg-app text-slate-400">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
          加载中...
        </div>
      </div>
    )
  }

  if (notFound || !summary) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-app">
        <EmptyState
          icon={<FolderOpen className="h-10 w-10" />}
          title="研究任务不存在"
          description="该 study 可能已被删除，或链接不正确。"
        />
        <Link to="/" className="text-sm text-primary-400 hover:text-primary-300 hover:underline">
          返回聊天
        </Link>
      </div>
    )
  }

  const status = summary.execution_status ?? 'unknown'
  const strategyName = summary.strategy_name ?? ''
  const workspacePath = summary.workspace_path ?? ''
  const progressPercent = summary.goal_snapshot?.progress_percent ?? 0
  const evidenceCount = summary.goal_snapshot?.evidence_count ?? 0
  const lastVerdict = summary.last_verdict ?? '—'

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
    status !== 'interrupted' && status !== 'early_stopped' &&
    status !== 'budget_limited'

  const controlActions = (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => navigate(-1)}
        className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-700 bg-slate-800/50 px-2 py-1.5 text-xs text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-200 active:scale-95"
        title="返回"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> 返回
      </button>
      {canPause && (
        <button
          onClick={() => onAction('pause')}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-amber-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-amber-500 active:scale-95 disabled:opacity-50"
        >
          <Pause className="h-3.5 w-3.5" /> 暂停
        </button>
      )}
      {canResume && (
        <button
          onClick={() => onAction('resume')}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-emerald-500 active:scale-95 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" /> 恢复
        </button>
      )}
      {canCancel && (
        <button
          onClick={() => onAction('cancel')}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-rose-700 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-rose-600 active:scale-95 disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" /> 取消
        </button>
      )}
    </div>
  )

  return (
    <PageShell
      title={summary.objective || '研究详情'}
      subtitle={`策略 ${strategyName || '—'} · 创建于 ${formatDateTime(summary.created_at)}`}
      icon={<BookOpen className="h-4 w-4" />}
      actions={controlActions}
    >
      {/* Error banner */}
      {error && (
        <div className="mb-4 rounded-xl border border-rose-800 bg-rose-950/50 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* KPI band */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          icon={<RotateCcw className="h-4 w-4" />}
          iconCls="border border-sky-500/30 bg-sky-500/10 text-sky-400"
          value={`${summary.current_round ?? 0}/${summary.max_rounds ?? 5}`}
          label="当前轮次 / 最大轮数"
          valueCls="text-sky-400"
        />
        <KpiCard
          icon={<Activity className="h-4 w-4" />}
          iconCls="border border-amber-500/30 bg-amber-500/10 text-amber-400"
          value={STUDY_STATUS_LABELS[status] ?? status}
          label="执行状态"
          valueCls={STUDY_STATUS_COLORS[status]?.split(' ')[0] ? 'text-slate-100' : 'text-slate-100'}
        />
        <KpiCard
          icon={<BarChart3 className="h-4 w-4" />}
          iconCls="border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          value={lastVerdict}
          label="最近 round 结论"
          valueCls="text-emerald-400"
        />
        <KpiCard
          icon={<Target className="h-4 w-4" />}
          iconCls="border border-primary-500/30 bg-primary-500/10 text-primary-400"
          value={`${progressPercent}%`}
          label={`目标进度 · ${evidenceCount} 证据`}
          valueCls="text-primary-400"
        />
      </div>

      {/* Body */}
      <main className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="min-w-0 space-y-4 xl:col-span-2">
          <ObjectiveProgress
            objective={summary.objective}
            progressPercent={progressPercent}
            evidenceCount={evidenceCount}
            criteria={summary.goal_snapshot?.criteria ?? []}
          />
          <RoundHistory
            rounds={summary.recent_rounds ?? []}
            currentRound={summary.current_round ?? 1}
            onOpenRun={openRun}
          />
          <MetricsCompare
            rounds={summary.recent_rounds ?? []}
            onOpenRun={openRun}
          />
          <ScoreboardMini scoreboard={summary.scoreboard ?? []} />
        </div>

        <div className="min-w-0 space-y-4 xl:sticky xl:top-4 xl:self-start">
          {/* Task info */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              <Info className="h-3 w-3" /> 任务信息
            </div>
            <div className="space-y-1.5 text-[10px]">
              <div className="flex items-center gap-2">
                <span className="flex w-14 flex-shrink-0 items-center gap-1 text-slate-600">
                  <FolderOpen className="h-3 w-3" /> 工作区
                </span>
                <span className="min-w-0 truncate font-mono text-slate-300" title={workspacePath}>
                  {workspacePath || '—'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex w-14 flex-shrink-0 items-center gap-1 text-slate-600">
                  <Clock className="h-3 w-3" /> 创建
                </span>
                <span className="font-mono text-slate-300">{formatDateTime(summary.created_at)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex w-14 flex-shrink-0 items-center gap-1 text-slate-600">
                  <Clock className="h-3 w-3" /> 更新
                </span>
                <span className="font-mono text-slate-300">{formatDateTime(summary.updated_at)}</span>
              </div>
              {summary.completed_at && (
                <div className="flex items-center gap-2">
                  <span className="flex w-14 flex-shrink-0 items-center gap-1 text-slate-600">
                    <Clock className="h-3 w-3" /> 完成
                  </span>
                  <span className="font-mono text-slate-300">{formatDateTime(summary.completed_at)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Directive input */}
          {(canPause || canResume) && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft space-y-2">
              <label className="block text-[10px] font-medium uppercase tracking-wider text-slate-500">
                注入研究方向（下一轮 researcher 看到）
              </label>
              <textarea
                rows={2}
                value={directiveText}
                onChange={(e) => setDirectiveText(e.target.value)}
                placeholder="例：改成动量因子 + 减小 top_n"
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
              />
              <button
                type="button"
                onClick={onDirective}
                disabled={submittingDirective || !directiveText.trim()}
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" /> 提交指令
              </button>
            </div>
          )}

          {/* Directives audit trail */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              <Clock className="h-3 w-3" /> 指令记录
            </div>
            {(directives?.directives?.length ?? 0) === 0 ? (
              <p className="text-xs text-slate-500">暂无指令</p>
            ) : (
              <ul className="space-y-1.5">
                {directives!.directives.map((d) => (
                  <li key={d.directive_id} className="rounded-lg border border-slate-800/60 bg-slate-950/60 p-2 text-[11px]">
                    <p className="text-slate-300">{d.content}</p>
                    <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-500">
                      <span>{formatDateTime(d.created_at)}</span>
                      {d.issued_by && <span>· {d.issued_by}</span>}
                      <span
                        className={
                          d.consumed_at
                            ? 'rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 text-emerald-400'
                            : 'rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 text-amber-400'
                        }
                      >
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
    </PageShell>
  )
}
