import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Clock, ArrowRight, RefreshCw } from 'lucide-react'
import { IconNav } from '../components/layout/IconNav'
import { StudyTab } from '../components/study/StudyTab'
import { EmptyState } from '../components/common/EmptyState'
import { api, type StudySummary } from '../api/client'
import { STUDY_STATUS_LABELS, STUDY_STATUS_COLORS } from '../components/study/constants'
import { useSessionStore } from '../stores/session'
import { useSystemStore } from '../stores/system'
import { useWorkflowStore } from '../stores/workflow'

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

  return (
    <div className="relative flex h-screen overflow-hidden bg-app">
      <div className="aurora-backdrop">
        <div className="grid-layer" />
        <div className="aurora-layer" />
        <div className="vignette-layer" />
        <div className="grain-layer" />
      </div>
      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <IconNav />
        <div className="flex flex-1 flex-col overflow-hidden">
          <header className="glass flex h-12 items-center gap-3 border-b border-slate-800 px-4">
            <BookOpen className="h-4 w-4 text-primary-400" />
            <h1 className="text-sm font-medium text-slate-200">Study 研究任务</h1>
            <span className="text-xs text-slate-500">
              9-agent 多轮自主研究（researcher → backtest → 验收）
            </span>
          </header>

          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 xl:flex-row">
            {/* Left: current session's study */}
            <section className="flex-1 min-w-0">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500">
                  当前会话
                </h2>
                {sessionId && (
                  <span className="truncate text-[10px] text-slate-600" title={sessionId}>
                    {sessionId.slice(0, 8)}…
                  </span>
                )}
              </div>
              {!sessionId ? (
                <EmptyState
                  icon={<BookOpen className="h-10 w-10" />}
                  title="尚未选择 session"
                  description="先在聊天页选择或创建一个 chat session"
                />
              ) : (
                <StudyTab sessionId={sessionId} workspacePath={workspacePath} />
              )}
            </section>

            {/* Right: history list */}
            <aside className="w-full xl:w-80 flex-shrink-0">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500">
                  历史研究
                </h2>
                <button
                  onClick={() => void loadList()}
                  disabled={loadingList}
                  className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-800 hover:text-slate-300 disabled:opacity-50"
                  title="刷新"
                >
                  <RefreshCw className={`h-3 w-3 ${loadingList ? 'animate-spin' : ''}`} />
                  刷新
                </button>
              </div>
              {studies.length === 0 ? (
                <div className="rounded border border-slate-800/50 bg-slate-900/30 p-4 text-center text-xs text-slate-500">
                  {loadingList ? '加载中...' : '暂无历史研究任务'}
                </div>
              ) : (
                <ul className="space-y-2">
                  {studies.map((s) => {
                    const status = s.execution_status ?? 'unknown'
                    return (
                      <li key={s.study_id}>
                        <button
                          onClick={() => navigate(`/study/${s.study_id}`)}
                          className="w-full rounded-lg border border-slate-800/50 bg-slate-900/30 p-2.5 text-left transition-colors hover:border-slate-700 hover:bg-slate-900/60"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs text-slate-200">
                              {s.objective || '未命名研究'}
                            </span>
                            <ArrowRight className="h-3 w-3 flex-shrink-0 text-slate-500" />
                          </div>
                          <div className="mt-1.5 flex flex-wrap items-center gap-2">
                            <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium ${STUDY_STATUS_COLORS[status] ?? 'bg-slate-700 text-slate-100'}`}>
                              {STUDY_STATUS_LABELS[status] ?? status}
                            </span>
                            <span className="text-[10px] text-slate-500">
                              Round {s.current_round ?? 0}
                            </span>
                            <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-600">
                              <Clock className="h-2.5 w-2.5" />
                              {formatDateTime(s.updated_at ?? s.created_at)}
                            </span>
                          </div>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </aside>
          </div>
        </div>
      </div>
    </div>
  )
}
