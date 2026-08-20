import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, Pause, Play, X, FolderOpen,
  RotateCcw, BarChart3, BookOpen,
  Archive, ArchiveRestore, Edit3, LayoutGrid,
  ChevronDown, ChevronRight,
} from 'lucide-react'
import { api, type StudySummaryResponse, type StudyAvailableActionsResponse } from '../../api/client'
import { STUDY_STATUS_LABELS } from './constants'
import { EmptyState } from '../common/EmptyState'
import { PageShell } from '../layout/PageShell'
import { EditObjectiveDialog } from './EditObjectiveDialog'
import { formatDateTime, clampRound } from './utils'
import { ContinueDialog } from './ContinueDialog'
import { AgentApprovalDialog } from './AgentApprovalDialog'
import { useSSE } from '../../hooks/useSSE'
import { StudyChat } from './dashboard/widgets/StudyChat'
import { MetricsCompare } from './MetricsCompare'
import { RoundHistory } from './RoundHistory'

export function StudyDetailPage() {
  const { studyId = '' } = useParams<{ studyId: string }>()
  const [searchParams] = useSearchParams()
  const showEditLayout = searchParams.get('editLayout') === 'true'
  const navigate = useNavigate()
  const [summary, setSummary] = useState<StudySummaryResponse | null>(null)
  const [actions, setActions] = useState<StudyAvailableActionsResponse | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editObjectiveOpen, setEditObjectiveOpen] = useState(false)
  const [continueDialogOpen, setContinueDialogOpen] = useState(false)
  const [roundHistoryOpen, setRoundHistoryOpen] = useState(false)

  const summaryRef = useRef<StudySummaryResponse | null>(null)
  const etagRef = useRef<string | null>(null)

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
        const st = summaryRef.current?.execution_status
        const isTerminal = ['complete', 'cancelled', 'archived'].includes(st ?? '')
        timer = isTerminal ? null : setTimeout(poll, 10_000)
      }
    }

    void poll()
    void loadActions()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [studyId, loadActions, loadSummary])

  useSSE(studyId)

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
  const currentRound = clampRound(summary.current_round)

  const bestCalmar = (summary.recent_rounds ?? [])
    .map((r) => r.metrics?.calmar)
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v))
    .reduce((a, b) => Math.max(a, b), 0)

  const canPause = (actions?.actions ?? []).some((a) => a.name === 'pause')
  const canContinue = (actions?.actions ?? []).some((a) => a.name === 'continue')
  const canCancel = (actions?.actions ?? []).some((a) => a.name === 'cancel')
  const canArchive = (actions?.actions ?? []).some((a) => a.name === 'archive')
  const canUnarchive = (actions?.actions ?? []).some((a) => a.name === 'unarchive')
  const canReplaceObjective = (actions?.actions ?? []).some(
    (a) => a.name === 'replace_objective',
  )

  const subtitle = [
    strategyName || '—',
    formatDateTime(summary.created_at),
    `R${currentRound}/${summary.max_rounds ?? 5}`,
    `${STUDY_STATUS_LABELS[status] ?? status}`,
    `Calmar ${bestCalmar.toFixed(2)}`,
  ].join(' · ')

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
      {showEditLayout && (
        <button
          className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-700 bg-slate-800/50 px-2.5 py-1.5 text-xs text-slate-400 transition-all hover:border-slate-600 hover:text-slate-200 active:scale-95"
        >
          <LayoutGrid className="h-3.5 w-3.5" /> 编辑布局
        </button>
      )}
    </div>
  )

  return (
    <PageShell
      title={summary.objective || '研究详情'}
      subtitle={subtitle}
      icon={<BookOpen className="h-4 w-4" />}
      actions={controlActions}
    >
      {/* Error banner */}
      {error && (
        <div className="mb-4 rounded-xl border border-rose-800 bg-rose-950/50 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* Main content: left StudyChat + right panel */}
      <div className="flex min-h-0 flex-1 gap-4" style={{ height: 'calc(100vh - 120px)' }}>
        {/* Left: StudyChat */}
        <div className="min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
          <StudyChat studyId={studyId} summary={summary} />
        </div>

        {/* Right panel */}
        <div className="flex w-80 flex-shrink-0 flex-col gap-3">
          {/* MetricsCompare */}
          <div className="min-h-0 flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
              <BarChart3 className="h-3 w-3" /> 指标对比
            </div>
            <div className="max-h-52 overflow-y-auto">
              <MetricsCompare rounds={summary.recent_rounds ?? []} />
            </div>
          </div>

          {/* Placeholder chart area */}
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-900/20 p-4">
            <span className="text-xs text-slate-600">图表区（待添加）</span>
          </div>

          {/* RoundHistory — collapsible */}
          <div className="min-h-0 flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/40">
            <button
              onClick={() => setRoundHistoryOpen((v) => !v)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500 transition-colors hover:bg-slate-800/40"
            >
              {roundHistoryOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              <RotateCcw className="h-3 w-3" /> 轮次历史
              <span className="ml-auto text-slate-600">{summary.recent_rounds?.length ?? 0} 轮</span>
            </button>
            {roundHistoryOpen && (
              <div className="max-h-60 overflow-y-auto border-t border-slate-800 px-3 py-2">
                <RoundHistory
                  rounds={summary.recent_rounds ?? []}
                  currentRound={summary.current_round}
                  studyId={studyId}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      <EditObjectiveDialog
        studyId={studyId}
        currentObjective={summary.objective}
        goalId={summary.goal_snapshot?.goal_id ?? null}
        open={editObjectiveOpen}
        onClose={() => setEditObjectiveOpen(false)}
        onSuccess={() => {
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
