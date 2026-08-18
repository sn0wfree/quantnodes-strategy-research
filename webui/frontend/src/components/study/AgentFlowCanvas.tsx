import { useState, useCallback, useMemo, useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type NodeTypes,
  type EdgeTypes,
  BackgroundVariant,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { GitBranch } from 'lucide-react'
import { api } from '../../api/client'
import { DAGNode, type DAGNodeData } from '../workflow/DAGNode'
import { DAGEdge } from '../workflow/DAGEdge'
import { layoutWithDagre } from '../workflow/layout'
import { AgentNodeDetail } from './AgentNodeDetail'
import { DAGProgressBar } from '../workflow/DAGProgressBar'

interface AgentFlowCanvasProps {
  studyId: string
  currentRound: number
  totalRounds?: number
}

const AGENT_SEQUENCE = [
  { id: 'researcher', label: 'Researcher', type: 'llm_agent', agentColor: '#38bdf8' },
  { id: 'data_quality', label: 'Data Quality', type: 'evaluator', agentColor: '#34d399' },
  { id: 'factor_analyst', label: 'Factor Analyst', type: 'llm_agent', agentColor: '#38bdf8' },
  { id: 'strategist', label: 'Strategist', type: 'planner', agentColor: '#a78bfa' },
  { id: 'portfolio_construction', label: 'Portfolio', type: 'llm_agent', agentColor: '#38bdf8' },
  { id: 'risk_controller', label: 'Risk Control', type: 'evaluator', agentColor: '#34d399' },
  { id: 'attribution_analyst', label: 'Attribution', type: 'evaluator', agentColor: '#34d399' },
  { id: 'anti_overfit_analyst', label: 'Anti-Overfit', type: 'evaluator', agentColor: '#34d399' },
]

export function AgentFlowCanvas({ studyId, currentRound, totalRounds }: AgentFlowCanvasProps) {
  const [selectedNode, setSelectedNode] = useState<(DAGNodeData & { id: string }) | null>(null)
  const [agentStatuses, setAgentStatuses] = useState<Record<string, { status: string; duration_s?: number }>>({})
  const [loading, setLoading] = useState(false)

  const loadAgentStatuses = useCallback(async () => {
    if (!studyId || currentRound <= 0) return
    setLoading(true)
    try {
      const r = await api.study.roundManifest(studyId, currentRound)
      const manifest = r.manifest as Record<string, unknown> | undefined
      const agentOutputs = manifest?.agent_outputs as Record<string, unknown> | undefined

      const statuses: Record<string, { status: string; duration_s?: number }> = {}
      let doneCount = 0

      for (const agent of AGENT_SEQUENCE) {
        const isDone = !!agentOutputs?.[agent.id]
        if (isDone) doneCount++
        statuses[agent.id] = { status: isDone ? 'completed' : 'pending' }
      }

      // Mark current running agent
      if (doneCount < AGENT_SEQUENCE.length) {
        statuses[AGENT_SEQUENCE[doneCount].id].status = 'running'
      }

      setAgentStatuses(statuses)
    } catch {
      // Manifest may not exist yet
    } finally {
      setLoading(false)
    }
  }, [studyId, currentRound])

  useEffect(() => {
    void loadAgentStatuses()
  }, [loadAgentStatuses])

  // Build nodes and edges for ReactFlow
  const rawNodes = useMemo(() => {
    return AGENT_SEQUENCE.map((agent) => ({
      id: agent.id,
      label: agent.label,
      status: (agentStatuses[agent.id]?.status ?? 'pending') as DAGNodeData['status'],
      agentColor: agent.agentColor,
      agentName: agent.id,
      type: agent.type,
    }))
  }, [agentStatuses])

  const rawEdges = useMemo(() => {
    return AGENT_SEQUENCE.slice(0, -1).map((agent, i) => ({
      source: agent.id,
      target: AGENT_SEQUENCE[i + 1].id,
    }))
  }, [])

  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => layoutWithDagre(rawNodes, rawEdges),
    [rawNodes, rawEdges]
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)

  useEffect(() => {
    const { nodes: newNodes, edges: newEdges } = layoutWithDagre(rawNodes, rawEdges)
    setNodes(newNodes)
    setEdges(newEdges)
  }, [rawNodes, rawEdges, setNodes, setEdges])

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNode(node.data as unknown as DAGNodeData & { id: string })
    },
    []
  )

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes: EdgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  const doneCount = Object.values(agentStatuses).filter((s) => s.status === 'completed').length
  const progress = Math.round((doneCount / AGENT_SEQUENCE.length) * 100)

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 shadow-soft overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <GitBranch className="h-3 w-3 text-primary-400" />
          Agent 流水线 · Round {currentRound}
          {loading && (
            <div className="ml-2 h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
          )}
        </div>
        {totalRounds != null && (
          <span className="font-mono text-[9px] text-slate-600">共 {totalRounds} 轮</span>
        )}
      </div>

      {/* ReactFlow Canvas */}
      <div className="h-[300px]">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          proOptions={{ hideAttribution: true }}
          minZoom={0.3}
          maxZoom={2}
        >
          <Background variant={BackgroundVariant.Dots} gap={26} size={1.2} color="var(--canvas-grid)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {/* Progress bar */}
      <div className="border-t border-slate-800 px-4 py-2">
        <DAGProgressBar
          progress={progress}
          completed={doneCount}
          total={AGENT_SEQUENCE.length}
        />
      </div>

      {/* Detail modal */}
      {selectedNode && (
        <AgentNodeDetail
          agentId={selectedNode.id}
          studyId={studyId}
          currentRound={currentRound}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  )
}
