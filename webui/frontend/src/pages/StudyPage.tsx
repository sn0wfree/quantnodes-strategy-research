import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Clock, ArrowRight, RefreshCw, Activity } from 'lucide-react'
import { PageShell } from '../components/layout/PageShell'
import { StudyTab } from '../components/study/StudyTab'
import { EmptyState } from '../components/common/EmptyState'
import { api, type StudySummary } from '../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from '../components/study/constants'
import { useSessionStore } from '../stores/session'
import { useSystemStore } from '../stores/system'
import { useWorkflowStore } from '../stores/workflow'

const ACTIVE_STATUSES = ['running', 'queued', 'monitoring', 'paused']

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

function ShimmerCard() {
  return (
    <div className="animate-pulse rounded-xl border border-slate-800/50 bg-slate-900/40 p-3">
      <div className="h-3 w-2/3 rounded bg-slate-800/70" />
      <div className="mt-2 h-2.5 w-1/2 rounded bg-slate-800/50" />
    </div>
  )
}

function HistoryCard({
  study,
  onClick,
}: {
  study: StudySummary
  onClick: () => void
}) {
  const status = study.execution_status ?? 'unknown'
  const isActive = ACTIVE_STATUSES.includes(status)
  const lastMetrics = study.last_metrics
  const fmtMetric = (key: string) => {
    const v = lastMetrics?.[key]
    return v != null ? Number(v).toFixed(2) : null
  }
  const c = fmtMetric('calmar')
  const s = fmtMetric('sharpe')
  const d = fmtMetric('max_dd')
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full cursor-pointer rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-left shadow-soft transition-all duration-200 hover:translate-x-0.5 hover:border-slate-700 hover:bg-slate-900/80 hover:shadow-elevated active:translate-x-0 active:scale-[0.99]"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs font-medium text-slate-200">
          {study.objective || '未命名研究'}
        </span>
        <ArrowRight className="h-3 w-3 flex-shrink-0 text-slate-600 transition-colors group-hover:text-slate-400" />
      </div>
      {study.strategy_name && (
        <span className="mt-1 inline-block font-mono text-[10px] text-primary-400/90">
          {study.strategy_name}
        </span>
      )}
      {(c != null || s != null || d != null) && (
        <div className="mt-1.5 flex items-center gap-2 font-mono text-[9px] tabular-nums text-slate-500">
          {s != null && <span className="text-sky-400/80">S {s}</span>}
          {c != null && <span className="text-primary-400/80">C {c}</span>}
          {d != null && <span className="text-rose-400/70">DD {d}</span>}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9px] font-medium ${
            STUDY_STATUS_COLORS[status] ?? 'border-slate-700 bg-slate-800 text-slate-300'
          }`}
        >
          {isActive && (
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
          )}
          {STUDY_STATUS_LABELS[status] ?? status}
        </span>
        {study.last_verdict && (
          <span
            className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${
              study.last_verdict === 'keep'
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400'
                : 'border-slate-700 bg-slate-800/70 text-slate-500'
            }`}
          >
            {study.last_verdict}
            {study.last_verdict === 'keep' && ' ✓'}
          </span>
        )}
        <span className="text-[10px] text-slate-500">Round {study.current_round ?? 0}</span>
        <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-600">
          <Clock className="h-2.5 w-2.5" />
          {formatDateTime(study.updated_at ?? study.created_at)}
        </span>
      </div>
    </button>
  )
}

/**
 * Standalone Study page (moved out of the chat right panel): create a
 * study for the current session, watch its live progress, and browse
 * historical studies (click through to the detail page).
 */
export function StudyPage() {
  const navigate = useNavigate()
  const sessionId = useSessionStore((s) => s.currentSessionId)

  // Resolve workspace path (same precedence as the old right panel)
  const systemWorkspacePath = useSystemStore((s) => s.workspacePath)
  const presets = useWorkflowStore((s) => s.presets)
  const currentPresetId = useWorkflowStore((s) => s.currentPresetId)
  const currentPreset = presets.find((p) => p.id === currentPresetId)
  const workspacePath =
    systemWorkspacePath
    || (currentPreset as unknown as { workspace_path?: string })?.workspace_path
    || ''

  const [studies, setStudies] = useState<StudySummary[]>([])
  const [loadingList, setLoadingList] = useState(false)

  const loadList = useCallback(async () => {
    setLoadingList(true)
    try {
      const res = await api.study.list({ limit: 20 })
      setStudies(res.studies ?? [])
    } catch {
      // Non-critical — history list can be empty
    } finally {
      setLoadingList(false)
    }
  }, [])

  useEffect(() => {
    void loadList()
  }, [loadList])

  const refreshBtn = (
    <button
      onClick={() => void loadList()}
      disabled={loadingList}
      className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/50 px-2.5 py-1.5 text-xs text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-300 active:scale-95 disabled:opacity-50"
      title="刷新"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${loadingList ? 'animate-spin' : ''}`} />
      刷新
    </button>
  )

  return (
    <PageShell
      title="Study 研究任务"
      subtitle="9-agent 多轮自主研究 · researcher → backtest → 验收"
      icon={<BookOpen className="h-4 w-4" />}
      actions={refreshBtn}
    >
      <div className="grid flex-1 gap-5 xl:grid-cols-[1fr_320px]">
        {/* Left: current session's study */}
        <section className="min-w-0">
          <div className="mb-2.5 flex items-center justify-between">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <Activity className="h-3.5 w-3.5 text-primary-400" />
              当前会话
            </h2>
            {sessionId && (
              <span className="truncate font-mono text-[10px] text-slate-600" title={sessionId}>
                {sessionId.slice(0, 8)}…
              </span>
            )}
          </div>
          <div className="rounded-xl border border-slate-800/60 bg-slate-900/40 p-4 shadow-soft">
            {!sessionId ? (
              <EmptyState
                icon={<BookOpen className="h-10 w-10" />}
                title="尚未选择 session"
                description="先在聊天页选择或创建一个 chat session"
              />
            ) : (
              <StudyTab sessionId={sessionId} workspacePath={workspacePath} />
            )}
          </div>
        </section>

        {/* Right: history list */}
        <aside className="min-w-0">
          <div className="mb-2.5 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              历史研究
            </h2>
            <span className="font-mono text-[10px] text-slate-600">{studies.length}</span>
          </div>
          {loadingList && studies.length === 0 ? (
            <div className="space-y-2">
              <ShimmerCard />
              <ShimmerCard />
            </div>
          ) : studies.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-800 px-4 py-10 text-center text-xs text-slate-600">
              暂无历史研究任务
            </div>
          ) : (
            <ul className="space-y-2">
              {studies.map((s) => (
                <li key={s.study_id}>
                  <HistoryCard
                    study={s}
                    onClick={() => navigate(`/study/${s.study_id}`)}
                  />
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </PageShell>
  )
}
