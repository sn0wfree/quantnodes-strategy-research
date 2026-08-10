import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  ReactFlowProvider,
  type Connection,
  type Node,
  type Edge,
  type NodeTypes,
  type OnNodesChange,
  type OnEdgesChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Bot,
  CalendarCheck,
  ClipboardList,
  Gauge,
  Code2,
  Wrench,
  Trash2,
  Plus,
  Search,
  LayoutGrid,
  Undo2,
  Redo2,
} from 'lucide-react'
import { DAGNode, type DAGNodeData } from './DAGNode'
import { DAGEdge } from './DAGEdge'
import { layoutWithDagre } from './layout'
import type { DefinitionEdge, DefinitionNode } from '../../api/client'

// ── Node palette (mirrors backend NODE_METADATA) ───────────────

export const NODE_PALETTE = [
  {
    type: 'llm_agent',
    label: '子 Agent',
    icon: Bot,
    color: '#38bdf8',
    desc: '完整 chat 子 agent（角色+提示词+工具）',
    defaults: { role: 'researcher' },
  },
  {
    type: 'planner',
    label: '生成计划',
    icon: ClipboardList,
    color: '#a78bfa',
    desc: '目标 → 3-8 步研究子图',
    defaults: { max_steps: 6 },
  },
  {
    type: 'evaluator',
    label: '评估进度',
    icon: Gauge,
    color: '#34d399',
    desc: 'continue / replan / stop 决策',
    defaults: {},
  },
  {
    type: 'approval',
    label: '人工确认',
    icon: CalendarCheck,
    color: '#fbbf24',
    desc: '暂停等待用户审批（图切点）',
    defaults: {},
  },
  {
    type: 'python',
    label: 'Python 函数',
    icon: Code2,
    color: '#f472b6',
    desc: '调用注册的 Python 函数',
    defaults: { function: '' },
  },
  {
    type: 'tool',
    label: '调用工具',
    icon: Wrench,
    color: '#fb923c',
    desc: '直接调用注册工具（run_backtest 等）',
    defaults: { tool: 'run_backtest' },
  },
] as const

export type PaletteItem = (typeof NODE_PALETTE)[number]

const TYPE_META = Object.fromEntries(NODE_PALETTE.map((p) => [p.type, p])) as Record<string, PaletteItem>

const CONFIG_FIELDS: Record<string, Array<{ key: string; label: string; type: 'text' | 'select' | 'number'; options?: string[]; placeholder?: string }>> = {
  llm_agent: [
    { key: 'role', label: '角色', type: 'select',
      options: ['researcher', 'data_quality', 'factor_analyst', 'strategist', 'backtest_diagnostics', 'critic'] },
    { key: 'prompt_text', label: '附加指令', type: 'text', placeholder: '节点专属任务指令' },
    { key: 'max_iterations', label: '迭代上限', type: 'number' },
  ],
  planner: [
    { key: 'max_steps', label: '计划步数 (3-8)', type: 'number' },
  ],
  evaluator: [],
  approval: [
    { key: 'timeout', label: '超时秒 (空=永久等待)', type: 'number' },
  ],
  python: [
    { key: 'function', label: '函数名', type: 'text', placeholder: '已注册的 Python 函数' },
  ],
  tool: [
    { key: 'tool', label: '工具名', type: 'select',
      options: ['run_backtest', 'get_market_data', 'check_data', 'clean_data', 'compute_factor', 'factor_analysis', 'search_symbol'] },
  ],
}

const DRAG_DATA_KEY = 'application/x-workflow-node-type'

interface WorkflowEditorProps {
  nodes: DefinitionNode[]
  edges: DefinitionEdge[]
  onSave: (nodes: DefinitionNode[], edges: DefinitionEdge[]) => void
  saving?: boolean
}

function buildRfNode(n: DefinitionNode): Node {
  const meta = TYPE_META[n.type] as PaletteItem | undefined
  return {
    id: n.id,
    position: { x: 100 + Math.random() * 120, y: 100 + Math.random() * 120 },
    data: {
      label: (n.label || meta?.label) ?? n.id,
      status: 'pending' as const,
      agentName: n.id,
      type: n.type,
      agentColor: meta?.color,
      config: n.config,
    } as DAGNodeData,
  }
}

export function WorkflowEditor({ nodes, edges, onSave, saving }: WorkflowEditorProps) {
  return (
    <ReactFlowProvider>
      <WorkflowEditorInner nodes={nodes} edges={edges} onSave={onSave} saving={saving} />
    </ReactFlowProvider>
  )
}

function WorkflowEditorInner({ nodes, edges, onSave, saving }: WorkflowEditorProps) {
  const [rfNodes, setRfNodes, onNodesChangeRaw] = useNodesState(nodes.map(buildRfNode))
  const [rfEdges, setRfEdges, onEdgesChangeRaw] = useEdgesState<Edge>(
    edges.map((e, i) => ({ id: `e-${i}`, source: e.source, target: e.target, type: 'dagEdge' }) as Edge),
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  const undoStack = useRef<Array<{ nodes: Node[]; edges: Edge[] }>>([])
  const redoStack = useRef<Array<{ nodes: Node[]; edges: Edge[] }>>([])
  const dragTypeRef = useRef<string | null>(null)
  const instance = useReactFlow()

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  // ── undo / redo ──────────────────────────────────────────────

  const snapshot = useCallback(() => ({ nodes: rfNodes, edges: rfEdges }), [rfNodes, rfEdges])

  const pushHistory = useCallback(() => {
    undoStack.current.push({ nodes: rfNodes.map((n) => ({ ...n, position: { ...n.position } })), edges: rfEdges })
    redoStack.current = []
    setCanUndo(true)
    setCanRedo(false)
  }, [rfNodes, rfEdges])

  const undo = useCallback(() => {
    const prev = undoStack.current.pop()
    if (!prev) return
    redoStack.current.push(snapshot())
    setRfNodes(prev.nodes)
    setRfEdges(prev.edges)
    setCanUndo(undoStack.current.length > 0)
    setCanRedo(true)
  }, [setRfNodes, setRfEdges, snapshot])

  const redo = useCallback(() => {
    const next = redoStack.current.pop()
    if (!next) return
    undoStack.current.push(snapshot())
    setRfNodes(next.nodes)
    setRfEdges(next.edges)
    setCanRedo(redoStack.current.length > 0)
    setCanUndo(true)
  }, [setRfNodes, setRfEdges, snapshot])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const typing = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
      if (typing || !(e.ctrlKey || e.metaKey)) return
      if (e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
      } else if (e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  // ── node / edge changes (push history for structural changes) ─

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      const structural = changes.some((c) => c.type === 'add' || c.type === 'remove')
      if (structural) pushHistory()
      else if (changes.some((c) => c.type === 'position' && c.dragging === false)) pushHistory()
      onNodesChangeRaw(changes)
    },
    [onNodesChangeRaw, pushHistory],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => {
      if (changes.some((c) => c.type === 'add' || c.type === 'remove')) pushHistory()
      onEdgesChangeRaw(changes)
    },
    [onEdgesChangeRaw, pushHistory],
  )

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || conn.source === conn.target) return
      setRfEdges((eds) => addEdge({ ...conn, type: 'dagEdge' }, eds))
      pushHistory()
    },
    [setRfEdges, pushHistory],
  )

  const addNode = useCallback(
    (type: string, position?: { x: number; y: number }) => {
      const meta = TYPE_META[type]
      if (!meta) return
      const id = `${type}_${Math.random().toString(36).slice(2, 6)}`
      const node: Node = {
        id,
        position: position ?? { x: 100 + Math.random() * 200, y: 150 + Math.random() * 150 },
        data: {
          label: meta.label,
          status: 'pending' as const,
          agentName: id,
          type,
          agentColor: meta.color,
          config: { ...meta.defaults },
        } as DAGNodeData,
      }
      setRfNodes((ns) => [...ns, node])
      setSelectedId(id)
      pushHistory()
    },
    [setRfNodes, pushHistory],
  )

  const removeNode = useCallback(
    (id: string) => {
      setRfNodes((ns) => ns.filter((n) => n.id !== id))
      setRfEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id))
      setSelectedId(null)
      pushHistory()
    },
    [setRfNodes, setRfEdges, pushHistory],
  )

  const updateConfig = useCallback(
    (id: string, patch: Record<string, unknown>) => {
      setRfNodes((ns) =>
        ns.map((n) =>
          n.id === id
            ? { ...n, data: { ...(n.data as DAGNodeData), config: { ...((n.data as DAGNodeData).config as object), ...patch } } }
            : n,
        ),
      )
    },
    [setRfNodes],
  )

  const updateLabel = useCallback(
    (id: string, label: string) => {
      setRfNodes((ns) =>
        ns.map((n) =>
          n.id === id ? { ...n, data: { ...(n.data as DAGNodeData), label } } : n,
        ),
      )
    },
    [setRfNodes],
  )

  // ── drop-to-add from palette ─────────────────────────────────

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const type = dragTypeRef.current ?? e.dataTransfer.getData(DRAG_DATA_KEY)
      if (!type || !TYPE_META[type]) return
      const pos = instance.screenToFlowPosition({ x: e.clientX, y: e.clientY })
      addNode(type, { x: pos.x - 90, y: pos.y - 35 })
      dragTypeRef.current = null
    },
    [instance, addNode],
  )

  // ── auto layout ──────────────────────────────────────────────

  const autoLayout = useCallback(() => {
    if (rfNodes.length === 0) return
    const { nodes: laidOut } = layoutWithDagre(
      rfNodes.map((n) => ({ id: n.id, ...(n.data as DAGNodeData) })),
      rfEdges.map((e) => ({ source: e.source, target: e.target })),
      { nodeType: 'dagNode', edgeType: 'dagEdge' },
    )
    pushHistory()
    setRfNodes((ns) =>
      ns.map((n) => {
        const laid = laidOut.find((l) => l.id === n.id)
        return laid ? { ...n, position: laid.position } : n
      }),
    )
  }, [rfNodes, rfEdges, setRfNodes, pushHistory])

  // ── save ─────────────────────────────────────────────────────

  const handleSave = () => {
    const defNodes: DefinitionNode[] = rfNodes.map((n) => {
      const d = n.data as DAGNodeData
      return {
        id: n.id,
        type: (d.type as DefinitionNode['type']) || 'llm_agent',
        label: (d.label as string) || '',
        config: ((d.config as object) ?? {}) as Record<string, unknown>,
      }
    })
    const defEdges: DefinitionEdge[] = rfEdges.map((e) => ({ source: e.source, target: e.target }))
    onSave(defNodes, defEdges)
  }

  const selected = rfNodes.find((n) => n.id === selectedId)
  const selectedData = selected?.data as DAGNodeData | undefined
  const selectedType = selectedData?.type as string | undefined
  const selectedConfig = (selectedData?.config as Record<string, unknown> | undefined) ?? {}

  const palette = useMemo(
    () =>
      search.trim()
        ? NODE_PALETTE.filter(
            (p) => p.label.includes(search) || p.type.includes(search) || p.desc.includes(search),
          )
        : NODE_PALETTE,
    [search],
  )

  return (
    <div className="flex h-full min-h-0">
      {/* Palette */}
      <aside className="flex w-56 shrink-0 flex-col gap-1 overflow-y-auto border-r border-slate-800 bg-slate-900/60 p-2">
        <div className="mb-1 flex items-center gap-1.5">
          <Search className="h-3 w-3 text-slate-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索节点…"
            className="w-full rounded border border-slate-800 bg-slate-950 px-2 py-1 text-[11px] text-slate-200 outline-none focus:border-primary-500"
          />
        </div>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">节点库</div>
        {palette.map((p) => {
          const Icon = p.icon
          return (
            <button
              key={p.type}
              draggable
              onDragStart={(e) => {
                dragTypeRef.current = p.type
                e.dataTransfer.setData(DRAG_DATA_KEY, p.type)
                e.dataTransfer.effectAllowed = 'move'
              }}
              onDragEnd={() => { dragTypeRef.current = null }}
              onClick={() => addNode(p.type)}
              className="group flex items-start gap-2 rounded border border-slate-700/60 bg-slate-900 px-2 py-1.5 text-left transition-colors hover:border-slate-500 hover:bg-slate-800"
              title={`${p.desc}\n点击添加，或拖拽到画布指定位置`}
            >
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded" style={{ backgroundColor: `${p.color}22`, color: p.color }}>
                <Icon className="h-3 w-3" />
              </span>
              <span>
                <span className="block text-xs text-slate-200">{p.label}</span>
                <span className="block text-[9px] text-slate-500">{p.type}</span>
              </span>
              <Plus className="ml-auto h-3 w-3 self-center text-slate-600 group-hover:text-slate-300" />
            </button>
          )
        })}
        <p className="mt-1 text-[9px] leading-relaxed text-slate-600">
          点击添加节点，或拖拽到画布指定位置。Ctrl+Z 撤销，Ctrl+Shift+Z 重做，Delete 删除选中。
        </p>
      </aside>

      {/* Canvas */}
      <div className="relative min-w-0 flex-1">
        {rfNodes.length === 0 && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/60 px-6 py-5 text-center">
              <div className="text-xs text-slate-300">空画布</div>
              <div className="mt-1 text-[11px] text-slate-500">
                从左侧节点库拖拽或点击添加节点，从节点右侧把手拖到目标左侧把手连线
              </div>
            </div>
          </div>
        )}
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodeClick={(_, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          onDragOver={onDragOver}
          onDrop={onDrop}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          maxZoom={1.5}
          deleteKeyCode={['Delete', 'Backspace']}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#334155" gap={24} />
          <Controls />
          <MiniMap pannable zoomable className="!bg-slate-900" />
        </ReactFlow>
        <div className="absolute bottom-4 left-3 z-10 flex gap-1">
          <button
            onClick={undo}
            disabled={!canUndo}
            title="撤销 (Ctrl+Z)"
            className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <Undo2 className="h-3 w-3" /> 撤销
          </button>
          <button
            onClick={redo}
            disabled={!canRedo}
            title="重做 (Ctrl+Shift+Z)"
            className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <Redo2 className="h-3 w-3" /> 重做
          </button>
          <button
            onClick={autoLayout}
            disabled={rfNodes.length === 0}
            title="dagre 自动布局"
            className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <LayoutGrid className="h-3 w-3" /> 自动布局
          </button>
        </div>
      </div>

      {/* Config panel */}
      <aside className="flex w-72 shrink-0 flex-col border-l border-slate-800 bg-slate-900/60 p-3">
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">节点配置</div>
        {selected && selectedData ? (
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-[10px] text-slate-400">名称</label>
              <input
                value={(selectedData.label as string) ?? ''}
                onChange={(e) => updateLabel(selected.id, e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
              />
            </div>
            <div className="rounded bg-slate-950/60 px-2 py-1 text-[10px] text-slate-500">
              id: <span className="text-slate-400">{selected.id}</span> · type:{' '}
              <span className="text-slate-400">{selectedType}</span>
            </div>
            {CONFIG_FIELDS[selectedType ?? '']?.map((f) => (
              <div key={f.key}>
                <label className="mb-1 block text-[10px] text-slate-400">{f.label}</label>
                {f.type === 'select' ? (
                  <select
                    value={(selectedConfig[f.key] as string) ?? ''}
                    onChange={(e) => updateConfig(selected.id, { [f.key]: e.target.value })}
                    className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
                  >
                    <option value="">—</option>
                    {f.options?.map((o) => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={(selectedConfig[f.key] as string | number | undefined) ?? ''}
                    type={f.type === 'number' ? 'number' : 'text'}
                    placeholder={f.placeholder}
                    onChange={(e) => updateConfig(selected.id, { [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value })}
                    className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-primary-500"
                  />
                )}
              </div>
            ))}
            {selectedType === 'approval' && (
              <p className="text-[10px] text-amber-400/80">审批节点是图切点：上游段完成后暂停等待确认，超时保持等待。</p>
            )}
            <button
              onClick={() => removeNode(selected.id)}
              className="inline-flex w-full items-center justify-center gap-1 rounded border border-rose-800 bg-rose-950/40 px-2 py-1 text-xs text-rose-300 hover:bg-rose-950"
            >
              <Trash2 className="h-3 w-3" /> 删除节点
            </button>
          </div>
        ) : (
          <p className="text-xs text-slate-600">
            点击画布节点查看/编辑配置。连线：从节点右侧把手拖到目标节点左侧把手。
          </p>
        )}

        <div className="mt-auto space-y-1.5 border-t border-slate-800 pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full rounded bg-indigo-600 px-2 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存定义'}
          </button>
          <p className="text-[9px] text-slate-600">校验规则：6 种节点类型、planner/evaluator/approval 各最多 1 个、无环。</p>
        </div>
      </aside>
    </div>
  )
}
