import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, Clock, FolderOpen,
  Target, Activity, RotateCcw, BarChart3, BookOpen,
  GitBranch, MessageSquare, Archive, ArchiveRestore,
  Edit3, LayoutGrid,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyDirectivesResponse, type StudyAvailableActionsResponse } from '../../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from './constants'
import { EmptyState } from '../common/EmptyState'
import { PageShell } from '../layout/PageShell'
import { StudyFlowTab } from './StudyFlowTab'
import { EditObjectiveDialog } from './EditObjectiveDialog'
import { AgentChatLog } from './AgentChatLog'
import { formatDateTime, clampRound } from './utils'
import { ContinueDialog } from './ContinueDialog'
import { AgentApprovalDialog } from './AgentApprovalDialog'
import { DashboardGrid, WidgetPicker } from './dashboard'
import { useStudyDashboardStore } from '../../stores/studyDashboard'
import { useSSE } from '../../hooks/useSSE'

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
  const [actions, setActions] = useState<StudyAvailableActionsResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [directiveText, setDirectiveText] = useState('')
  const [submittingDirective, setSubmittingDirective] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('overview')
  const [editObjectiveOpen, setEditObjectiveOpen] = useState(false)
  const [continueDialogOpen, setContinueDialogOpen] = useState(false)
  const [logsSelectedRound, setLogsSelectedRound] = useState<number>(1)

  // Refs for ETag-conditional polling and terminal-state detection
  const summaryRef = useRef<StudySummaryResponse | null>(null)
  const etagRef = useRef<string | null>(null)

  const loadDirectives = useCallback(async () => {
    try {
      const r = await api.study.directives(studyId)
      setDirectives(r)
    } catch {
      // Non-critical — audit trail can be absent
    }
  }, [studyId])

  const loadSummary = useCallback(async () => {
    try {
      const { data, etag } = await api.study.summaryWithEtag(studyId, etagRef.current ?? undefined)
      if (data) {
        setSummary(data)
        summaryRef.current = data
        etagRef.current = etag
        setNotFound(false)
        setError('')
      }
      // data === null → 304 Not Modified, no update needed
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
        // Stop polling when study reaches a terminal status
        const st = summaryRef.current?.execution_status
        const isTerminal = ['complete', 'cancelled', 'archived'].includes(st ?? '')
        timer = isTerminal ? null : setTimeout(poll, 10_000)
      }
    }

    void poll()
    void loadDirectives()
    void loadActions()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [
    studyId,
    loadDirectives,
    loadActions,
    loadSummary,
  ])

  // ── Dashboard layout store ────────────────────────────────────
  const loadDashboard = useStudyDashboardStore(s => s.load)
  const dashboardEditMode = useStudyDashboardStore(s => s.editMode)
  const setDashboardEditMode = useStudyDashboardStore(s => s.setEditMode)

  // ── SSE connection for study events ────────────────────────────
  useSSE(studyId)

  useEffect(() => {
    loadDashboard(studyId)
    return () => useStudyDashboardStore.getState().clear()
  }, [studyId, loadDashboard])

  const onAction = async (
    action: 'pause' | 'continue' | 'cancel' | 'archive' | 'unarchive',
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

  const onContinue = async (mode: 'resume' | 'restart', fromRound?: number) => {
    setBusy(true)
    try {
      await api.study.dispatchAction(studyId, 'continue', {
        mode: mode === 'restart' ? 'restart' : 'append',
        ...(fromRound ? { from_round: fromRound } : {}),
      })
      await loadActions()
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

  const canPause = (actions?.actions ?? []).some((a) => a.name === 'pause')
  const canContinue = (actions?.actions ?? []).some((a) => a.name === 'continue')
  const canCancel = (actions?.actions ?? []).some((a) => a.name === 'cancel')
  const canArchive = (actions?.actions ?? []).some((a) => a.name === 'archive')
  const canUnarchive = (actions?.actions ?? []).some((a) => a.name === 'unarchive')
  const canReplaceObjective = (actions?.actions ?? []).some(
    (a) => a.name === 'replace_objective',
  )
  const canDirective = canPause || canContinue

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
      {canContinue && (
        <button
          onClick={() => setContinueDialogOpen(true)}
          disabled={busy}
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-emerald-500 active:scale-95 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" /> 继续
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

      {/* Dashboard layout toggle */}
      <button
        onClick={() => setDashboardEditMode(!dashboardEditMode)}
        className={`inline-flex cursor-pointer items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs transition-all active:scale-95 ${
          dashboardEditMode
            ? 'bg-emerald-600 text-white hover:bg-emerald-500'
            : 'border border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-200'
        }`}
      >
        <LayoutGrid className="h-3.5 w-3.5" /> {dashboardEditMode ? '完成' : '编辑布局'}
      </button>

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
        <div className="mt-4 flex gap-4">
          {/* Widget Picker (edit mode only) */}
          {dashboardEditMode && <WidgetPicker />}

          {/* Dashboard Grid */}
          <div className="min-w-0 flex-1">
            <DashboardGrid studyId={studyId} summary={summary} />
          </div>
        </div>
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

      <ContinueDialog
        open={continueDialogOpen}
        summary={summary}
        onClose={() => setContinueDialogOpen(false)}
        onContinue={onContinue}
      />

      <AgentApprovalDialog studyId={studyId} />
    </PageShell>
  )
}
