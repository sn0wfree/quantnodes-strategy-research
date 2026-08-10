import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, Plus, Save, Pencil, Trash2, Copy, RefreshCw, Loader2 } from 'lucide-react'
import { api, type DefinitionListItem, type DefinitionNode, type DefinitionEdge, type DefinitionRunSnapshot, type DefinitionNodeOutput } from '../../api/client'
import { useSessionStore } from '../../stores/session'
import { WorkflowEditor } from './WorkflowEditor'
import { ApprovalDialog } from './ApprovalDialog'
import { EmptyState } from '../common/EmptyState'

type RunStatus = 'pending' | 'running' | 'awaiting' | 'completed' | 'failed' | 'cancelled'

const RUN_STATUS_LABELS: Record<RunStatus, { text: string; cls: string }> = {
  pending: { text: '等待中', cls: 'bg-slate-800 text-slate-400' },
  running: { text: '运行中', cls: 'bg-sky-900/60 text-sky-300' },
  awaiting: { text: '等待审批', cls: 'bg-amber-900/60 text-amber-300' },
  completed: { text: '已完成', cls: 'bg-emerald-900/60 text-emerald-400' },
  failed: { text: '失败', cls: 'bg-rose-900/60 text-rose-300' },
  cancelled: { text: '已取消', cls: 'bg-slate-800 text-slate-400' },
}

export function DefinitionWorkflowPage() {
  const navigate = useNavigate()
  const sessionId = useSessionStore((s) => s.currentSessionId)

  const [definitions, setDefinitions] = useState<DefinitionListItem[]>([])
  const [editing, setEditing] = useState<{ name: string | null; nodes: DefinitionNode[]; edges: DefinitionEdge[] } | null>(null)
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const [objective, setObjective] = useState('')
  const [starting, setStarting] = useState(false)
  const [run, setRun] = useState<DefinitionRunSnapshot | null>(null)
  const [nodeOutputs, setNodeOutputs] = useState<DefinitionNodeOutput[]>([])
  const [runStatus, setRunStatus] = useState<RunStatus>('pending')
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [approvalPreview, setApprovalPreview] = useState('')
  const sseRef = useRef<EventSource | null>(null)

  const loadDefinitions = useCallback(async () => {
    try {
      const r = await api.definitions.list()
      setDefinitions(r.definitions ?? [])
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    void loadDefinitions()
  }, [loadDefinitions])

  useEffect(() => () => {
    sseRef.current?.close()
  }, [])

  const closeSSE = useCallback(() => {
    sseRef.current?.close()
    sseRef.current = null
  }, [])

  // ── Editing ─────────────────────────────────────────────────

  const startEdit = async (name: string) => {
    setError('')
    try {
      const r = await api.definitions.get(name)
      const d = r.definition
      setEditing({
        name: d.name,
        nodes: (d.nodes ?? []) as DefinitionNode[],
        edges: (d.edges ?? []) as DefinitionEdge[],
      })
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const startNew = () => {
    setNewName('')
    setEditing({ name: null, nodes: [], edges: [] })
  }

  const saveDefinition = async (nodes: DefinitionNode[], edges: DefinitionEdge[]) => {
    if (!editing) return
    setSaving(true)
    setError('')
    try {
      const name = editing.name ?? newName.trim()
      if (!name) {
        setError('请输入定义名称')
        return
      }
      await api.definitions.save({
        name,
        description: '',
        nodes,
        edges,
      })
      await loadDefinitions()
      setEditing(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const copyDefinition = async (name: string) => {
    setError('')
    try {
      await api.definitions.copy(name)
      await loadDefinitions()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const deleteDefinition = async (name: string) => {
    setError('')
    try {
      await api.definitions.remove(name)
      await loadDefinitions()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  // ── Running ─────────────────────────────────────────────────

  const startRun = async () => {
    if (!editing?.name || !objective.trim() || !sessionId) return
    setStarting(true)
    setError('')
    closeSSE()
    try {
      const r = await api.definitionRuns.start(sessionId, editing.name, objective.trim())
      applyRun(r.run_id, r.run)
      connectRunSSE(r.run_id)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setStarting(false)
    }
  }

  const applyRun = (runId: string, snap: DefinitionRunSnapshot) => {
    setRun(snap)
    setRunStatus(snap.status as RunStatus)
    setNodeOutputs([])
    void api.definitionRuns.detail(runId).then((d) => {
      setNodeOutputs(d.node_outputs ?? [])
    }).catch(() => {})
  }

  const connectRunSSE = (runId: string) => {
    closeSSE()
    const source = new EventSource(`/api/goal/workflow/run/${encodeURIComponent(runId)}/events`)
    sseRef.current = source

    const refresh = () => {
      void api.definitionRuns.status(runId).then((r) => {
        const snap = r.run as DefinitionRunSnapshot
        setRun(snap)
        setRunStatus(snap.status as RunStatus)
        if (snap.status === 'awaiting') {
          setApprovalPreview(
            `完成节点：${snap.completed_nodes.join('、') || '—'}`,
          )
          setApprovalOpen(true)
        } else {
          setApprovalOpen(false)
        }
      }).catch(() => {})
      void api.definitionRuns.detail(runId).then((d) => {
        setNodeOutputs(d.node_outputs ?? [])
      }).catch(() => {})
    }

    source.addEventListener('awaiting_approval', () => refresh())
    source.addEventListener('node_completed', () => refresh())
    source.addEventListener('plan_created', (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data) as { steps?: string[] }
        setApprovalPreview(`计划步骤：${d.steps?.join(' → ') || '—'}`)
      } catch { /* ignore */ }
    })
    source.addEventListener('run_completed', () => refresh())
    source.addEventListener('run_failed', () => refresh())
    source.addEventListener('run_terminal', () => {
      closeSSE()
      refresh()
    })
    source.onerror = () => {
      // polling SSE reconnects on its own; refresh state anyway
      refresh()
    }
  }

  const respondApproval = async (approved: boolean, edits?: string) => {
    if (!run) return
    setApprovalBusy(true)
    setError('')
    try {
      const r = await api.definitionRuns.approve(run.run_id, approved, edits ? { note: edits } : undefined)
      applyRun(run.run_id, r.run as DefinitionRunSnapshot)
      if ((r.run as DefinitionRunSnapshot).status === 'awaiting') {
        setApprovalPreview('已响应，等待下一轮审批')
      } else {
        setApprovalOpen(false)
      }
    } catch (err) {
      setError((err as Error).message)
      setApprovalOpen(false)
    } finally {
      setApprovalBusy(false)
    }
  }

  const resetRun = () => {
    closeSSE()
    setRun(null)
    setRunStatus('pending')
    setNodeOutputs([])
    setApprovalOpen(false)
    setObjective('')
  }

  const statusMeta = RUN_STATUS_LABELS[runStatus]

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
        <h1 className="text-sm font-medium text-slate-200">工作流编辑器</h1>
        {editing?.name && (
          <span className="truncate text-xs text-slate-400">· {editing.name}</span>
        )}
        {run && (
          <span className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${statusMeta.cls}`}>
            {statusMeta.text}
          </span>
        )}
        <div className="flex-1" />
        {editing?.name && run && runStatus === 'awaiting' && (
          <button
            onClick={() => setApprovalOpen(true)}
            className="rounded bg-amber-600 px-2 py-1 text-xs text-white hover:bg-amber-500"
          >
            待审批
          </button>
        )}
        {run && runStatus !== 'completed' && runStatus !== 'failed' && (
          <button
            onClick={resetRun}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-400 hover:bg-slate-800"
          >
            <RefreshCw className="h-3 w-3" /> 停止追踪
          </button>
        )}
      </header>

      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Sidebar */}
        <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-r border-slate-800 bg-slate-900/50 p-3">
          <button
            onClick={startNew}
            className="inline-flex items-center justify-center gap-1 rounded bg-indigo-600 px-2 py-1.5 text-xs text-white hover:bg-indigo-500"
          >
            <Plus className="h-3 w-3" /> 新建定义
          </button>

          <div>
            <div className="mb-1.5 text-[10px] uppercase text-slate-500">定义列表</div>
            {definitions.length === 0 ? (
              <p className="text-xs text-slate-600">暂无定义</p>
            ) : (
              <ul className="space-y-1">
                {definitions.map((d) => (
                  <li key={d.name}>
                    <div
                      onClick={() => startEdit(d.name)}
                      className={`group cursor-pointer rounded border px-2 py-1.5 ${
                        editing?.name === d.name
                          ? 'border-sky-500/40 bg-sky-500/10'
                          : 'border-slate-800 bg-slate-900 hover:border-slate-600'
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className={`rounded px-1 py-0.5 text-[9px] font-medium ${
                          d.source === 'builtin' ? 'bg-violet-900/60 text-violet-300' : 'bg-emerald-900/60 text-emerald-400'
                        }`}>
                          {d.source === 'builtin' ? '内置' : '用户'}
                        </span>
                        <span className="truncate text-xs text-slate-200">{d.name}</span>
                        <span className="ml-auto text-[9px] text-slate-600">{d.node_count} 节点</span>
                      </div>
                      {d.description && (
                        <div className="mt-0.5 truncate text-[10px] text-slate-500">{d.description}</div>
                      )}
                      <div className="mt-1 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <button
                          onClick={(e) => { e.stopPropagation(); startEdit(d.name) }}
                          className="rounded px-1 py-0.5 text-[9px] text-sky-300 hover:bg-slate-800" title="编辑"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); copyDefinition(d.name) }}
                          className="rounded px-1 py-0.5 text-[9px] text-slate-400 hover:bg-slate-800" title="复制到用户"
                        >
                          <Copy className="h-3 w-3" />
                        </button>
                        {d.source === 'user' && (
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteDefinition(d.name) }}
                            className="rounded px-1 py-0.5 text-[9px] text-rose-400 hover:bg-slate-800" title="删除"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {editing && (
            <div className="space-y-1.5 rounded border border-slate-700 bg-slate-900 p-2">
              {editing.name === null && (
                <div>
                  <label className="block text-[10px] text-slate-400">定义名称</label>
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="my_workflow"
                    className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
                  />
                </div>
              )}
              <label className="block text-[10px] text-slate-400">研究目标</label>
              <textarea
                rows={3}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="例：找出沪深300上 Sharpe > 1.5 的动量因子"
                className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
              />
              <button
                onClick={startRun}
                disabled={starting || !objective.trim() || !sessionId}
                className="w-full inline-flex items-center justify-center gap-1 rounded bg-emerald-600 px-2 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                <Play className="h-3 w-3" />
                {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : '启动运行'}
              </button>
              {!sessionId && (
                <p className="text-[10px] text-amber-500">需要先打开一个会话</p>
              )}
            </div>
          )}

          {run && (
            <div className="space-y-1.5 rounded border border-slate-700 bg-slate-900 p-2">
              <div className="text-[10px] uppercase text-slate-500">运行状态</div>
              <div className="flex items-center gap-2 text-xs text-slate-300">
                <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${statusMeta.cls}`}>
                  {statusMeta.text}
                </span>
                <span className="text-[9px] text-slate-500">{run.run_id}</span>
              </div>
              <div className="space-y-0.5 text-[10px] text-slate-400">
                <div>段：{run.segment_idx}/{run.segments_total} · 重规划：{run.replan_count}/{run.replan_max}</div>
                <div>已完成节点：{run.completed_nodes.length} 个</div>
                {run.failures.length > 0 && (
                  <div className="text-rose-400">失败：{run.failures.join('；')}</div>
                )}
              </div>
            </div>
          )}

          {nodeOutputs.length > 0 && (
            <div>
              <div className="mb-1.5 text-[10px] uppercase text-slate-500">节点输出</div>
              <ul className="space-y-1">
                {nodeOutputs.map((o) => (
                  <li key={o.node_id} className="rounded border border-slate-800 bg-slate-900 px-2 py-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 rounded-full ${
                        o.status === 'success' ? 'bg-emerald-400' : o.status === 'error' ? 'bg-rose-400' : 'bg-slate-500'
                      }`} />
                      <span className="truncate text-[10px] text-slate-300">{o.node_id}</span>
                      <span className="ml-auto text-[9px] text-slate-600">{o.elapsed_s}s</span>
                    </div>
                    {o.summary && (
                      <div className="mt-0.5 line-clamp-2 text-[9px] text-slate-500">{o.summary}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>

        {/* Main */}
        <main className="min-w-0 flex-1">
          {editing ? (
            <WorkflowEditor
              nodes={editing.nodes}
              edges={editing.edges}
              onSave={saveDefinition}
              saving={saving}
            />
          ) : (
            <div className="flex h-full items-center justify-center">
              <EmptyState
                icon={<Save className="h-10 w-10" />}
                title="编辑或新建工作流定义"
                description="从左侧选择定义进入编辑，或点击「新建定义」从零搭建设计（Dify 风格拖拽）"
              />
            </div>
          )}
        </main>
      </div>

      <ApprovalDialog
        open={approvalOpen}
        runId={run?.run_id ?? ''}
        planPreview={approvalPreview}
        busy={approvalBusy}
        onApprove={(edits) => respondApproval(true, edits)}
        onReject={(edits) => respondApproval(false, edits)}
        onClose={() => setApprovalOpen(false)}
      />
    </div>
  )
}
