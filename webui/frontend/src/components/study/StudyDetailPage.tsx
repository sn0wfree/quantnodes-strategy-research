import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, Send, Clock, FolderOpen,
  Target, Activity, RotateCcw, BarChart3, BookOpen, Info,
  ShieldAlert, GitBranch, MessageSquare, Archive, ArchiveRestore,
  Edit3,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyDirectivesResponse, type StudyHangingEventsResponse, type StudyAvailableActionsResponse, HANGING_EVENT_LABELS } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { ObjectiveProgress } from './ObjectiveProgress'
import { RoundHistory } from './RoundHistory'
import { ScoreboardMini } from './ScoreboardMini'
import { MetricsCompare } from './MetricsCompare'
import { MetricsTrendChart } from './MetricsTrendChart'
import { BudgetBar } from './BudgetBar'
import { EmptyState } from '../common/EmptyState'
import { PageShell } from '../layout/PageShell'
import { StudyFlowTab } from './StudyFlowTab'
import { EditObjectiveDialog } from './EditObjectiveDialog'
import { AgentChatLog } from './AgentChatLog'
import { formatDateTime, clampRound } from './utils'
import { RetryModeMenu } from './RetryModeMenu'
import { AgentApprovalDialog } from './AgentApprovalDialog'

type TabKey = 'overview' | 'flow' | 'logs'

const TABS: Array<{ key: TabKey; label: string; icon: React.ReactNode }> = [
  { key: 'overview', label: '概览', icon: <BarChart3 className="h-3 w-3" /> },
  { key: 'flow', label: '研究流程', icon: <GitBranch className="h-3 w-3" /> },
  { key: 'logs', label: '日志', icon: <MessageSquare className="h-3 w-3" /> },
]

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
  const [hanging, setHanging] = useState<StudyHangingEventsResponse | null>(null)
  const [actions, setActions] = useState<StudyAvailableActionsResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('overview')
  const [editObjectiveOpen, setEditObjectiveOpen] = useState(false)
  const [retryMenuOpen, setRetryMenuOpen] = useState(false)
  const [logsSelectedRound, setLogsSelectedRound] = useState<number>(1)

  const loadDirectives = useCallback(async () => {
    try {
      const r = await api.study.directives(studyId)
      setDirectives(r)
    } catch {
      // Non-critical — audit trail can be absent
    }
  }, [studyId])

  const loadHanging = useCallback(async () => {
    try {
      const r = await api.study.hangingEvents(studyId)
      setHanging(r)
    } catch {
      // Non-critical — observability panel can be absent
    }
  }, [studyId])

  const loadSummary = useCallback(async () => {
    try {
      const r = await api.study.summary(studyId)
      setSummary(r)
      setNotFound(false)
      setError('')
    } catch (err) {
      const status = (err as { status?: number })?.status
      if (status === 404) {
        setNotFound(true)
      } else {
        setError((err as Error).message)
      }
    }
  }, [studyId])

  const loadActions = useCallback(async () => {
    try {
      const r = await api.study.availableActions(studyId)
      setActions(r)
    } catch {
      setActions(null)
    }
  }, [studyId])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const poll = async () => {
      await loadSummary()
      if (!cancelled) {
        setLoading(false)
        timer = setTimeout(poll, 5000)
      }
    }

    void poll()
    void loadDirectives()
    void loadHanging()
    void loadActions()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [
    studyId,
    loadDirectives,
    loadHanging,
    loadActions,
    loadSummary,
  ])

  // Close retry menu on outside click
  const retryMenuRef = useCallback((el: HTMLDivElement | null) => {
    if (!el) return
    const handler = (e: MouseEvent) => {
      if (!el.contains(e.target as Node)) setRetryMenuOpen(false)
    }
    if (retryMenuOpen) {
      document.addEventListener('mousedown', handler, { once: true })
    }
  }, [retryMenuOpen])

  const onAction = async (
    action: 'pause' | 'resume' | 'resume_interrupted' | 'cancel' | 'archive' | 'unarchive',
    reason?: string,
  ) => {
    setBusy(true)
    try {
      await api.study.dispatchAction(studyId, action, reason ? { reason } : undefined)
      await loadActions()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onRetry = async (mode: 'append' | 'restart') => {
    setRetryMenuOpen(false)
    setBusy(true)
    try {
      await api.study.retry(studyId, undefined, mode)
      await loadActions()
      // Refresh summary to show new INTERRUPTED status
      await loadSummary()
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
  const metricTargets = summary.metric_targets ?? []

  const bestCalmar = (summary.recent_rounds ?? [])
    .map((r) => r.metrics?.calmar)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    .reduce((a, b) => Math.max(a, b), 0)
  const driftCount = summary.monitor_state?.drift_count ?? 0
  const isDrifting = status === 'needs_refresh' || driftCount > 0

  const openRun = (runName: string) => {
    if (!strategyName) return
    navigate(
      `/run/${encodeURIComponent(strategyName)}/${encodeURIComponent(runName)}`
    )
  }

  const canPause = (actions?.actions ?? []).some((a) => a.name === 'pause')
  const canResume = (actions?.actions ?? []).some(
    (a) => a.name === 'resume' || a.name === 'resume_interrupted'
  )
  const canCancel = (actions?.actions ?? []).some((a) => a.name === 'cancel')
  const canArchive = (actions?.actions ?? []).some((a) => a.name === 'archive')
  const canUnarchive = (actions?.actions ?? []).some((a) => a.name === 'unarchive')
  const canReplaceObjective = (actions?.actions ?? []).some(
    (a) => a.name === 'replace_objective',
  )
  const canRetry = (actions?.actions ?? []).some((a) => a.name === 'retry')
  const canDirective = canPause || canResume

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
          onClick={() =>
            onAction(
              (actions?.actions ?? []).some((a) => a.name === 'resume_interrupted')
                ? 'resume_interrupted'
                : 'resume'
            )
          }
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
          <X className="h-3.5 w-3.5" /> 中止
        </button>
      )}
      {canArchive && (
        <button
          onClick={() => {
            if (window.confirm('确定归档此研究？归档后默认列表不再显示，可在「显示已归档」中查看。')) {
              void onAction('archive')
            }
          }}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-amber-600/40 bg-amber-700/20 px-2.5 py-1.5 text-xs text-amber-200 transition-all hover:bg-amber-700/40 hover:text-amber-50 active:scale-95 disabled:opacity-50"
        >
          <Archive className="h-3.5 w-3.5" /> 归档
        </button>
      )}
      {canUnarchive && (
        <button
          onClick={() => {
            if (window.confirm('取消归档后，状态将变为「已中断」，可手动恢复运行。继续？')) {
              void onAction('unarchive')
            }
          }}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-sky-600/40 bg-sky-700/20 px-2.5 py-1.5 text-xs text-sky-200 transition-all hover:bg-sky-700/40 hover:text-sky-50 active:scale-95 disabled:opacity-50"
        >
          <ArchiveRestore className="h-3.5 w-3.5" /> 取消归档
        </button>
      )}
      {canReplaceObjective && (
        <button
          onClick={() => setEditObjectiveOpen(true)}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-indigo-500/40 bg-indigo-500/15 px-2.5 py-1.5 text-xs text-indigo-200 transition-all hover:bg-indigo-500/30 hover:text-indigo-50 active:scale-95 disabled:opacity-50"
        >
          <Edit3 className="h-3.5 w-3.5" /> 修改目标
        </button>
      )}
      {canRetry && (
        <div className="relative" ref={retryMenuRef}>
          <button
            onClick={() => setRetryMenuOpen((v) => !v)}
            disabled={busy}
            className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-sky-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-sky-500 active:scale-95 disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" /> 重试
          </button>
          {retryMenuOpen && (
            <RetryModeMenu
              onSelect={(mode) => {
                const label =
                  mode === 'append' ? '从下一轮继续？历史轮次将保留。' :
                  '从第 1 轮重试？历史轮次将被清除。'
                if (window.confirm(label)) {
                  void onRetry(mode)
                } else {
                  setRetryMenuOpen(false)
                }
              }}
            />
          )}
        </div>
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
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        <KpiCard
          icon={<RotateCcw className="h-4 w-4" />}
          iconCls="border border-sky-500/30 bg-sky-500/10 text-sky-400"
          value={`${clampRound(summary.current_round)}/${summary.max_rounds ?? 5}`}
          label="当前轮次 / 最大轮数"
          valueCls="text-sky-400"
        />
        <KpiCard
          icon={<Activity className="h-4 w-4" />}
          iconCls="border border-amber-500/30 bg-amber-500/10 text-amber-400"
          value={STUDY_STATUS_LABELS[status] ?? status}
          label={isDrifting ? `漂移 ×${driftCount}（需检查）` : '执行状态'}
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
        <KpiCard
          icon={<BarChart3 className="h-4 w-4" />}
          iconCls="border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          value={bestCalmar.toFixed(2)}
          label="最佳 Calmar（历史轮次）"
          valueCls="text-emerald-400"
        />
      </div>

      {/* Metric targets */}
      {metricTargets.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
            验收线:
          </span>
          {metricTargets.map((t, i) => (
            <span
              key={i}
              className="rounded-full border border-primary-500/30 bg-primary-500/10 px-2 py-0.5 font-mono text-[10px] text-primary-400"
            >
              {t.name} {t.op} {t.value}
            </span>
          ))}
        </div>
      )}

      {/* Tab navigation */}
      <div className="mt-4 flex items-center gap-1 border-b border-slate-800 pb-2">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setActiveTab(tab.key)}
            className={`inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === tab.key
                ? 'bg-primary-500/15 text-primary-300 border border-primary-500/40'
                : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800/50 border border-transparent'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'overview' && (
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
            currentRound={clampRound(summary.current_round)}
            onOpenRun={openRun}
            studyId={studyId}
          />
          <MetricsCompare
            rounds={summary.recent_rounds ?? []}
            onOpenRun={openRun}
          />
          <MetricsTrendChart
            rounds={summary.recent_rounds ?? []}
            metricTargets={summary.metric_targets ?? []}
          />
          {summary.budget && (
            <BudgetBar
              usedTurns={summary.budget.budget_used_turns}
              totalTurns={summary.budget.budget_turn}
              usedTimeS={summary.budget.budget_used_time_s}
              totalTimes={summary.budget.budget_time_seconds}
            />
          )}
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
          {(canDirective) && (
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

          {/* Hanging events (observability) */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <ShieldAlert className="h-3 w-3" /> 卡死防护事件
              </div>
              {hanging && hanging.recent.length > 0 && (
                <span className="rounded-full border border-rose-700/50 bg-rose-950/40 px-1.5 py-0.5 text-[9px] font-medium text-rose-400">
                  {hanging.recent.length} 个事件
                </span>
              )}
            </div>
            {!hanging || hanging.recent.length === 0 ? (
              <p className="text-xs text-slate-500">近 24h 无异常事件</p>
            ) : (
              <ul className="space-y-1.5">
                {hanging.recent.map((e, i) => (
                  <li
                    key={i}
                    className="rounded-lg border border-slate-800/60 bg-slate-950/60 p-2 text-[11px]"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-rose-400">
                        {HANGING_EVENT_LABELS[e.event_type] ?? e.event_type}
                      </span>
                      <span className="font-mono text-[9px] text-slate-600">
                        {new Date(e.created_at_iso).toLocaleTimeString()}
                      </span>
                    </div>
                    {e.detail && (
                      <p className="mt-0.5 truncate text-[10px] text-slate-500" title={e.detail}>
                        {e.detail}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

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
      )}

      {/* Flow tab - merged agents + todos */}
      {activeTab === 'flow' && (
        <StudyFlowTab
          studyId={studyId}
          summary={summary}
          directiveText={directiveText}
          submittingDirective={submittingDirective}
          canDirective={canDirective}
          onDirective={onDirective}
          onDirectiveTextChange={setDirectiveText}
        />
      )}

      {/* Logs tab - chat + directives + journal */}
      {activeTab === 'logs' && (
        <div className="mt-4 flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {/* Agent chat logs */}
            <AgentChatLog
              studyId={studyId}
              selectedRound={Math.max(1, logsSelectedRound || clampRound(summary.current_round))}
              onSelectedRoundChange={setLogsSelectedRound}
              totalRounds={summary.max_rounds}
            />

            {/* Directives audit trail */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 shadow-soft">
              <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <Clock className="h-3 w-3" /> 指令记录
              </div>
              {(directives?.directives?.length ?? 0) === 0 ? (
                <p className="text-xs text-slate-500">暂无指令</p>
              ) : (
                <ul className="space-y-1.5 max-h-96 overflow-y-auto">
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
        </div>
      )}

      <EditObjectiveDialog
        studyId={studyId}
        currentObjective={summary.objective}
        goalId={summary.goal_snapshot?.goal_id ?? null}
        open={editObjectiveOpen}
        onClose={() => setEditObjectiveOpen(false)}
        onSuccess={() => {
          // Refresh summary so the new objective + history show immediately
          void loadSummary()
          void loadActions()
        }}
      />

      <AgentApprovalDialog studyId={studyId} />
    </PageShell>
  )
}
