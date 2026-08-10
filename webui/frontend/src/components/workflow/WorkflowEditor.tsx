import { useCallback, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type NodeTypes,
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
} from 'lucide-react'
import { DAGNode, type DAGNodeData } from './DAGNode'
import { DAGEdge } from './DAGEdge'
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

const TYPE_META = Object.fromEntries(NODE_PALETTE.map((p) => [p.type, p]))

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

interface WorkflowEditorProps {
  nodes: DefinitionNode[]
  edges: DefinitionEdge[]
  onSave: (nodes: DefinitionNode[], edges: DefinitionEdge[]) => void
  saving?: boolean
}

export function WorkflowEditor({ nodes, edges, onSave, saving }: WorkflowEditorProps) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState(
    nodes.map((n) => ({
      id: n.id,
      position: { x: 100 + Math.random() * 60, y: 100 + Math.random() * 60 },
      data: {
        label: (n.label || TYPE_META[n.type]?.label) ?? n.id,
        status: 'pending' as const,
        agentName: n.id,
        type: n.type,
        config: n.config,
      } as DAGNodeData,
    })),
  )
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState(
    edges.map((e, i) => ({ id: `e-${i}`, source: e.source, target: e.target, type: 'dagEdge' })),
  )
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || conn.source === conn.target) return
      setRfEdges((eds) => addEdge({ ...conn, type: 'dagEdge' }, eds))
    },
    [setRfEdges],
  )

  const addNode = useCallback(
    (type: string) => {
      const meta = TYPE_META[type as keyof typeof TYPE_META]
      if (!meta) return
      const id = `${type}_${Math.random().toString(36).slice(2, 6)}`
      setRfNodes((ns) => [
        ...ns,
        {
          id,
          position: { x: 200 + Math.random() * 200, y: 150 + Math.random() * 150 },
          data: {
            label: meta.label,
            status: 'pending' as const,
            agentName: id,
            type,
            agentColor: meta.color,
            config: { ...meta.defaults },
          } as DAGNodeData,
        },
      ])
      setSelectedId(id)
    },
    [setRfNodes],
  )

  const removeNode = useCallback(
    (id: string) => {
      setRfNodes((ns) => ns.filter((n) => n.id !== id))
      setRfEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id))
      setSelectedId(null)
    },
    [setRfNodes, setRfEdges],
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

  return (
    <div className="flex h-full min-h-0">
      {/* Palette */}
      <aside className="flex w-52 shrink-0 flex-col gap-1 overflow-y-auto border-r border-slate-800 bg-slate-900/60 p-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">节点库</div>
        {NODE_PALETTE.map((p) => {
          const Icon = p.icon
          return (
            <button
              key={p.type}
              onClick={() => addNode(p.type)}
              className="group flex items-start gap-2 rounded border border-slate-700/60 bg-slate-900 px-2 py-1.5 text-left transition-colors hover:border-slate-500 hover:bg-slate-800"
              title={p.desc}
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
      </aside>

      {/* Canvas */}
      <div className="min-w-0 flex-1">
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
          fitView
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#334155" gap={24} />
          <Controls />
          <MiniMap pannable zoomable className="!bg-slate-900" />
        </ReactFlow>
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
