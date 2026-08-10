import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Play, Plus, Save, Pencil, Trash2, Copy, RefreshCw, Loader2, FileJson,
  Boxes, ListChecks, ChevronDown, ChevronUp,
} from 'lucide-react'
import { api, type DefinitionListItem, type DefinitionNode, type DefinitionEdge, type DefinitionRunSnapshot, type DefinitionNodeOutput, type DefinitionPayload } from '../../api/client'
import { useSessionStore } from '../../stores/session'
import { WorkflowEditor, NODE_PALETTE } from './WorkflowEditor'
import { ApprovalDialog } from './ApprovalDialog'
import { ImportDefinitionDialog } from './ImportDefinitionDialog'
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
  const [editingName, setEditingName] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')
  const [error, setError] = useState('')

  const [sidebarTab, setSidebarTab] = useState<'palette' | 'defs'>('palette')
  const [runOpen, setRunOpen] = useState(false)
  const [objective, setObjective] = useState('')
  const [starting, setStarting] = useState(false)
  const [run, setRun] = useState<DefinitionRunSnapshot | null>(null)
  const [nodeOutputs, setNodeOutputs] = useState<DefinitionNodeOutput[]>([])
  const [runStatus, setRunStatus] = useState<RunStatus>('pending')
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [approvalPreview, setApprovalPreview] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
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
      setEditingName(d.name)
      setSavedAt('')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const startNew = () => {
    setEditing({ name: null, nodes: [], edges: [] })
    setEditingName('')
    setSavedAt('')
  }

  const saveDefinition = async (nodes: DefinitionNode[], edges: DefinitionEdge[]) => {
    if (!editing) return
    setSaving(true)
    setError('')
    try {
      const name = editing.name ?? editingName.trim()
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
      // stay in edit mode; name may have been assigned on first save
      setEditing((prev) => (prev ? { ...prev, name } : prev))
      setEditingName(name)
      setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
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
      if (editing?.name === name) setEditing(null)
      await loadDefinitions()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  // ── JSON import ────────────────────────────────────────────

  const importToCanvas = (payload: DefinitionPayload) => {
    closeSSE()
    resetRun()
    setError('')
    setEditing({
      name: payload.name || null,
      nodes: (payload.nodes ?? []) as DefinitionNode[],
      edges: (payload.edges ?? []) as DefinitionEdge[],
    })
    setEditingName(payload.name || '')
    setSavedAt('')
    setImportOpen(false)
  }

  const importAndSave = async (payload: DefinitionPayload) => {
    if (!payload.name) {
      setError('导入保存需要 name 字段')
      return
    }
    setImportBusy(true)
    setError('')
    try {
      await api.definitions.save({
        name: payload.name,
        description: payload.description,
        version: payload.version,
        budget: payload.budget,
        llm: payload.llm,
        params: payload.params,
        nodes: payload.nodes,
        edges: payload.edges,
      })
      setImportOpen(false)
      await loadDefinitions()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setImportBusy(false)
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
      setRunOpen(true)
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
    setRunOpen(false)
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
        <div className="flex min-w-0 items-center gap-2">
          {editing ? (
            <>
              <input
                value={editingName}
                onChange={(e) => setEditingName(e.target.value)}
                placeholder="定义名称"
                className="w-40 rounded border border-transparent bg-transparent px-1.5 py-0.5 text-sm font-medium text-slate-200 outline-none hover:border-slate-700 focus:border-primary-500"
              />
              {editing.name && (
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  definitions.find((d) => d.name === editing.name)?.source === 'builtin'
                    ? 'bg-violet-900/60 text-violet-300'
                    : 'bg-emerald-900/60 text-emerald-400'
                }`}>
                  {definitions.find((d) => d.name === editing.name)?.source === 'builtin' ? '内置' : '用户'}
                </span>
              )}
              {savedAt && <span className="text-[10px] text-emerald-400">已保存 {savedAt}</span>}
            </>
          ) : (
            <h1 className="text-sm font-medium text-slate-200">工作流编辑器</h1>
          )}
        </div>
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
        {editing && (
          <button
            onClick={() => setRunOpen(true)}
            disabled={!editing.name || !sessionId}
            title={!sessionId ? '需要先打开一个会话' : '启动运行'}
            className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            <Play className="h-3 w-3" /> 运行
          </button>
        )}
      </header>

      {error && (
        <div className="mx-4 mt-2 rounded border border-rose-800 bg-rose-950/50 px-3 py-1.5 text-xs text-rose-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Sidebar with tabs */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900/50">
          <div className="flex border-b border-slate-800">
            <button
              onClick={() => setSidebarTab('palette')}
              className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-xs ${
                sidebarTab === 'palette' ? 'border-b-2 border-primary-500 text-slate-100' : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              <Boxes className="h-3.5 w-3.5" /> 节点库
            </button>
            <button
              onClick={() => setSidebarTab('defs')}
              className={`flex flex-1 items-center justify-center gap-1.5 py-2 text-xs ${
                sidebarTab === 'defs' ? 'border-b-2 border-primary-500 text-slate-100' : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              <ListChecks className="h-3.5 w-3.5" /> 定义库
            </button>
          </div>

          {sidebarTab === 'palette' ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              <p className="mb-2 text-[10px] leading-relaxed text-slate-600">
                点击或拖拽到画布添加节点，从节点右侧把手拖到目标左侧把手连线。
              </p>
              <div className="space-y-1">
                {NODE_PALETTE.map((p) => {
                  const Icon = p.icon
                  return (
                    <div key={p.type} className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className="flex h-5 w-5 items-center justify-center rounded" style={{ backgroundColor: `${p.color}22`, color: p.color }}>
                          <Icon className="h-3 w-3" />
                        </span>
                        <span className="text-xs text-slate-200">{p.label}</span>
                        <span className="ml-auto text-[9px] text-slate-600">{p.type}</span>
                      </div>
                      <div className="mt-0.5 pl-7 text-[10px] text-slate-500">{p.desc}</div>
                    </div>
                  )
                })}
              </div>
              <div className="mt-3 flex gap-1.5">
                <button
                  onClick={startNew}
                  className="flex-1 inline-flex items-center justify-center gap-1 rounded bg-indigo-600 px-2 py-1.5 text-xs text-white hover:bg-indigo-500"
                >
                  <Plus className="h-3 w-3" /> 新建定义
                </button>
                <button
                  onClick={() => setImportOpen(true)}
                  className="inline-flex items-center justify-center gap-1 rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
                >
                  <FileJson className="h-3 w-3" /> 导入
                </button>
              </div>
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
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
          )}
        </aside>

        {/* Main */}
        <main className="min-w-0 flex-1">
          {editing ? (
            <WorkflowEditor
              key={editing.name ?? `new-${editingName}`}
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
                description="从左侧「定义库」选择定义进入编辑，或点击「新建定义」从零搭建（拖拽式画布）"
              />
            </div>
          )}
        </main>
      </div>

      {/* Bottom run drawer */}
      {runOpen && (
        <div className="border-t border-slate-800 bg-slate-900/80">
          <div className="flex items-center gap-3 px-4 pt-2">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">运行面板</div>
            <button
              onClick={() => setRunOpen(false)}
              className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-slate-400 hover:bg-slate-800"
            >
              <ChevronDown className="h-3 w-3" /> 折叠
            </button>
          </div>
          <div className="flex items-start gap-3 px-4 py-2">
            <div className="flex-1">
              <textarea
                rows={1}
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                placeholder="例：找出沪深300上 Sharpe > 1.5 的动量因子"
                className="w-full resize-none rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-primary-500"
              />
              {!sessionId && (
                <p className="mt-1 text-[10px] text-amber-500">需要先打开一个会话</p>
              )}
            </div>
            <button
              onClick={startRun}
              disabled={starting || !editing?.name || !objective.trim() || !sessionId}
              className="inline-flex items-center gap-1 rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              <Play className="h-3 w-3" />
              {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : '启动运行'}
            </button>
            {run && (
              <button
                onClick={resetRun}
                className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800"
              >
                <RefreshCw className="h-3 w-3" /> 停止追踪
              </button>
            )}
          </div>
          {run && (
            <div className="flex flex-wrap items-center gap-3 px-4 pb-2 text-[11px]">
              <span className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${statusMeta.cls}`}>
                {statusMeta.text}
              </span>
              <span className="text-slate-500">{run.run_id}</span>
              <span className="text-slate-400">段：{run.segment_idx}/{run.segments_total}</span>
              <span className="text-slate-400">重规划：{run.replan_count}/{run.replan_max}</span>
              <span className="text-slate-400">已完成节点：{run.completed_nodes.length}</span>
              {run.failures.length > 0 && (
                <span className="text-rose-400">失败：{run.failures.join('；')}</span>
              )}
              <button
                onClick={() => setRunOpen(false)}
                className="ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-[10px] text-slate-500 hover:bg-slate-800"
              >
                节点输出 {nodeOutputs.length > 0 ? `(${nodeOutputs.length})` : ''}
                <ChevronDown className="h-3 w-3" />
              </button>
            </div>
          )}
          {run && nodeOutputs.length > 0 && (
            <div className="flex gap-2 overflow-x-auto px-4 pb-2">
              {nodeOutputs.map((o) => (
                <div key={o.node_id} className="w-52 shrink-0 rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      o.status === 'success' ? 'bg-emerald-400' : o.status === 'error' ? 'bg-rose-400' : 'bg-slate-500'
                    }`} />
                    <span className="truncate text-[10px] text-slate-300">{o.node_id}</span>
                    <span className="ml-auto text-[9px] text-slate-600">{o.elapsed_s}s</span>
                  </div>
                  {o.summary && (
                    <div className="mt-0.5 line-clamp-2 text-[9px] text-slate-500">{o.summary}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Collapsed run toggle */}
      {!runOpen && run && (
        <button
          onClick={() => setRunOpen(true)}
          className="flex items-center gap-2 border-t border-slate-800 bg-slate-900/80 px-4 py-1.5 text-[10px] text-slate-400 hover:bg-slate-800"
        >
          <ChevronUp className="h-3 w-3" />
          <span className={`rounded px-1 py-0.5 font-medium ${statusMeta.cls}`}>{statusMeta.text}</span>
          <span className="text-slate-500">{run.run_id}</span>
        </button>
      )}

      <ApprovalDialog
        open={approvalOpen}
        runId={run?.run_id ?? ''}
        planPreview={approvalPreview}
        busy={approvalBusy}
        onApprove={(edits) => respondApproval(true, edits)}
        onReject={(edits) => respondApproval(false, edits)}
        onClose={() => setApprovalOpen(false)}
      />

      <ImportDefinitionDialog
        open={importOpen}
        busy={importBusy}
        onImportToCanvas={importToCanvas}
        onSave={importAndSave}
        onClose={() => setImportOpen(false)}
      />
    </div>
  )
}
