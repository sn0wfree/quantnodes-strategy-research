import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, History, RefreshCw } from 'lucide-react'
import { api, type WorkflowListItem } from '../../api/client'
import { useSessionStore } from '../../stores/session'
import { WorkflowDAG } from './WorkflowDAG'
import type { DAGNodeData } from './DAGNode'
import { EmptyState } from '../common/EmptyState'

type WfStatus = 'idle' | 'running' | 'paused' | 'completed' | 'failed'

const GOAL_STATUS_LABELS: Record<string, string> = {
  active: '进行中',
  complete: '已完成',
  cancelled: '已取消',
  in_review: '审核中',
}

interface HistoryGoal {
  goal_id: string
  workflow_id: string
  objective: string
  goal_status: string
  created_at: string
}

function mapAgentStatus(s: string): DAGNodeData['status'] {
  switch (s) {
    case 'success': return 'completed'
    case 'failed': return 'failed'
    case 'skipped': return 'skipped'
    case 'running': return 'running'
    default: return 'pending'
  }
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

export function WorkflowPage() {
  const navigate = useNavigate()
  const sessionId = useSessionStore((s) => s.currentSessionId)

  const [presets, setPresets] = useState<WorkflowListItem[]>([])
  const [history, setHistory] = useState<HistoryGoal[]>([])
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [graph, setGraph] = useState<{ nodes: DAGNodeData[]; edges: Array<{ source: string; target: string }> } | null>(null)
  const [objective, setObjective] = useState('')
  const [starting, setStarting] = useState(false)
  const [goalId, setGoalId] = useState<string | null>(null)
  const [goalLabel, setGoalLabel] = useState('')
  const [wfStatus, setWfStatus] = useState<WfStatus>('idle')
  const [progress, setProgress] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loadingHistory, setLoadingHistory] = useState(false)

  const loadPresets = useCallback(async () => {
    try {
      const r = await api.workflow.list()
      setPresets(r.workflows ?? [])
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true)
    try {
      const r = await api.goal.list({ limit: 100 })
      const wf = (r.goals ?? []).filter(
        (g): g is HistoryGoal & typeof g => Boolean(g.workflow_id)
      )
      setHistory(
        wf.map((g) => ({
          goal_id: g.goal_id,
          workflow_id: g.workflow_id as string,
          objective: g.objective,
          goal_status: g.goal_status,
          created_at: g.created_at,
        })),
      )
    } catch {
      // Non-critical — history list can be absent
    } finally {
      setLoadingHistory(false)
    }
  }, [])

  useEffect(() => {
    void loadPresets()
    void loadHistory()
  }, [loadPresets, loadHistory])

  const loadGraph = useCallback(async (name: string) => {
    const r = await api.workflow.graph(name)
    setGraph({
      nodes: r.nodes.map((n) => ({
        label: n.label,
        status: 'pending' as const,
        agentName: n.id,
      })),
      edges: r.edges,
    })
    return r
  }, [])

  const selectPreset = async (name: string) => {
    setSelectedName(name)
    setGoalId(null)
    setGoalLabel('')
    setWfStatus('idle')
    setProgress(0)
    setError('')
    try {
      await loadGraph(name)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  // ── SSE connection for a running workflow ──────────────────────

  const connectSSE = useCallback((gid: string) => {
    const source = new EventSource(`/api/goal/workflow/events?goal_id=${encodeURIComponent(gid)}`)

    const applyProgress = (data: Record<string, any>) => {
      if (data.status === 'running' || data.status === 'paused') {
        setWfStatus(data.paused ? 'paused' : 'running')
      } else if (data.status === 'completed') {
        setWfStatus('completed')
      } else if (data.status === 'error') {
        setWfStatus('failed')
      }
      const total = Number(data.agents_total ?? 0)
      const done = Number(data.agents_completed ?? 0)
      if (total > 0) setProgress(Math.round((done / total) * 100))
      if (data.agent_statuses) {
        setGraph((g) => {
          if (!g) return g
          return {
            ...g,
            nodes: g.nodes.map((n) => {
              const st = data.agent_statuses[n.agentName ?? '']
              return st ? { ...n, status: mapAgentStatus(st) } : n
            }),
          }
        })
      }
    }

    source.addEventListener('progress', (e) => {
      try {
        applyProgress(JSON.parse((e as MessageEvent).data))
      } catch {
        // Ignore malformed frames
      }
    })
    source.addEventListener('dag_update', (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data) as { node_id?: string; status?: string }
        if (!d.node_id) return
        setGraph((g) => {
          if (!g) return g
          return {
            ...g,
            nodes: g.nodes.map((n) =>
              n.agentName === d.node_id && d.status
                ? { ...n, status: mapAgentStatus(d.status) }
                : n
            ),
          }
        })
      } catch {
        // Ignore malformed frames
      }
    })
    source.onerror = () => {
      // EventSource auto-reconnects; terminal states stop the stream.
      source.close()
    }
    return source
  }, [])

  const startWorkflow = async () => {
    if (!selectedName || !objective.trim()) return
    setStarting(true)
    setError('')
    try {
      const r = await api.workflow.start(sessionId ?? '', selectedName, objective.trim())
      setGoalId(r.goal_id)
      setGoalLabel(objective.trim())
      connectSSE(r.goal_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStarting(false)
    }
  }

  // ── Resume a historical goal ───────────────────────────────────

  const openHistoryGoal = async (g: HistoryGoal) => {
    setSelectedName(g.workflow_id)
    setGoalId(g.goal_id)
    setGoalLabel(g.objective)
    setWfStatus('idle')
    setProgress(0)
    setError('')
    try {
      await loadGraph(g.workflow_id)
      const r = await api.workflow.status(g.goal_id)
      if (r.status === 'ok' && r.progress) {
        const p = r.progress
        setWfStatus(p.paused ? 'paused' : p.status === 'completed' ? 'completed' : p.status === 'error' ? 'failed' : 'running')
        const total = Number(p.agents_total ?? 0)
        const done = Number(p.agents_completed ?? 0)
        if (total > 0) setProgress(Math.round((done / total) * 100))
        if (p.agent_statuses) {
          setGraph((g2) => {
            if (!g2) return g2
            return {
              ...g2,
              nodes: g2.nodes.map((n) => {
                const st = p.agent_statuses![n.agentName ?? '']
                return st ? { ...n, status: mapAgentStatus(st) } : n
              }),
            }
          })
        }
        // Re-attach SSE for live updates (only while the runner is alive)
        if (p.status === 'running' || p.paused) {
          connectSSE(g.goal_id)
        }
      } else if (g.goal_status === 'complete') {
        setWfStatus('completed')
      }
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const onPause = async () => {
    if (!goalId) return
    setBusy(true)
    try {
      await api.workflow.pause(goalId)
      setWfStatus('paused')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onResume = async () => {
    if (!goalId) return
    setBusy(true)
    try {
      await api.workflow.resume(goalId)
      setWfStatus('running')
      connectSSE(goalId)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onReset = () => {
    setSelectedName(null)
    setGoalId(null)
    setGoalLabel('')
    setGraph(null)
    setWfStatus('idle')
    setProgress(0)
    setObjective('')
  }

  const completedCount = graph?.nodes.filter((n) => n.status === 'completed').length ?? 0

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100">
      {/* Top bar */}
      <header className="flex items-center gap-3 border-b border-slate-800 bg-slate-900/80 px-4 py-2.5">
        <button
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> 返回
        </button>
        <h1 className="text-sm font-medium text-slate-200">工作流</h1>
        {goalLabel && (
          <span className="truncate text-xs text-slate-400">· {goalLabel}</span>
        )}
        <div className="flex-1" />
        {goalId && (wfStatus === 'running' || wfStatus === 'paused') && (
          <div className="flex items-center gap-1.5">
            {wfStatus === 'running' ? (
              <button
                onClick={onPause}
                disabled={busy}
                className="inline-flex items-center gap-1 rounded bg-amber-600 px-2 py-1 text-xs hover:bg-amber-500 disabled:opacity-50"
              >
                <Play className="h-3 w-3 rotate-90" /> 暂停
              </button>
            ) : (
              <button
                onClick={onResume}
                disabled={busy}
                className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500 disabled:opacity-50"
              >
                <Play className="h-3 w-3" /> 恢复
              </button>
            )}
          </div>
        )}
        <button
          onClick={onReset}
          className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
        >
          <RefreshCw className="h-3 w-3" /> 重置
        </button>
      </header>

      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Left sidebar */}
        <aside className="flex w-72 flex-col gap-3 overflow-y-auto border-r border-slate-800 bg-slate-900/50 p-3">
          {/* Presets */}
          <div>
            <div className="mb-1.5 text-[10px] uppercase text-slate-500">工作流模板</div>
            {presets.length === 0 ? (
              <p className="text-xs text-slate-500">暂无模板</p>
            ) : (
              <ul className="space-y-1">
                {presets.map((p) => (
                  <li key={p.name}>
                    <button
                      onClick={() => selectPreset(p.name)}
                      className={`w-full rounded px-2 py-1.5 text-left text-xs transition-colors ${
                        selectedName === p.name
                          ? 'bg-primary-600/20 text-sky-300 border border-primary-500/40'
                          : 'text-slate-300 hover:bg-slate-800 border border-transparent'
                      }`}
                    >
                      <div className="truncate">{p.name}</div>
                      {p.description && (
                        <div className="mt-0.5 truncate text-[10px] text-slate-500">
                          {p.description}
                        </div>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Start form */}
          {selectedName && (
            <div className="space-y-1.5 rounded border border-slate-700 bg-slate-900 p-2">
              <label className="block text-[10px] text-slate-400">研究目标</label>
              <textarea
                rows={3}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="例：找出沪深300上 Sharpe > 1.5 的动量因子"
                className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
              />
              <button
                onClick={startWorkflow}
                disabled={starting || !objective.trim() || !sessionId}
                className="w-full inline-flex items-center justify-center gap-1 rounded bg-indigo-600 px-2 py-1.5 text-xs hover:bg-indigo-500 disabled:opacity-50"
              >
                <Play className="h-3 w-3" />
                {starting ? '启动中...' : '启动工作流'}
              </button>
              {!sessionId && (
                <p className="text-[10px] text-amber-500">需要先打开一个会话</p>
              )}
            </div>
          )}

          {/* History */}
          <div>
            <div className="mb-1.5 flex items-center gap-1 text-[10px] uppercase text-slate-500">
              <History className="h-3 w-3" /> 历史目标
              {loadingHistory && (
                <RefreshCw className="h-3 w-3 animate-spin" />
              )}
            </div>
            {history.length === 0 ? (
              <p className="text-xs text-slate-500">暂无历史工作流</p>
            ) : (
              <ul className="space-y-1">
                {history.map((g) => (
                  <li key={g.goal_id}>
                    <button
                      onClick={() => openHistoryGoal(g)}
                      className={`w-full rounded px-2 py-1.5 text-left text-xs transition-colors ${
                        goalId === g.goal_id
                          ? 'bg-slate-800 text-slate-100'
                          : 'text-slate-300 hover:bg-slate-800'
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`rounded px-1 py-0.5 text-[9px] font-medium ${
                            g.goal_status === 'complete'
                              ? 'bg-emerald-900/50 text-emerald-400'
                              : g.goal_status === 'active'
                                ? 'bg-sky-900/50 text-sky-300'
                                : 'bg-slate-800 text-slate-400'
                          }`}
                        >
                          {GOAL_STATUS_LABELS[g.goal_status] ?? g.goal_status}
                        </span>
                        <span className="ml-auto text-[9px] text-slate-500">
                          {formatTime(g.created_at)}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[10px] text-slate-400">{g.objective}</div>
                      <div className="truncate text-[9px] text-slate-600">{g.workflow_id}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Main DAG */}
        <main className="min-w-0 flex-1">
          {graph ? (
            <WorkflowDAG
              workflowName={selectedName ?? '工作流'}
              nodes={graph.nodes}
              edges={graph.edges}
              status={wfStatus}
              progress={progress}
              completed={completedCount}
              total={graph.nodes.length}
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <EmptyState
                icon={<Play className="h-10 w-10" />}
                title="选择工作流模板"
                description="从左侧选择一个模板并设定目标后启动"
              />
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
