import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes,
  type EdgeTypes,
  BackgroundVariant,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { GitBranch } from 'lucide-react'
import { api, type StudyGraphResponse, type StudyRoundAgentOutputsResponse } from '../../api/client'
import { layoutWithDagre } from '../workflow/layout'
import { DAGNode, type DAGNodeData } from '../workflow/DAGNode'
import { DAGEdge } from '../workflow/DAGEdge'
import { DAGProgressBar } from '../workflow/DAGProgressBar'
import { AgentNodeDetail } from './AgentNodeDetail'
import { RoundPicker } from './RoundPicker'

interface AgentFlowCanvasProps {
  studyId: string
  selectedRound: number
  onSelectedRoundChange: (round: number) => void
  totalRounds?: number
}

const AGENT_COLORS: Record<string, string> = {
  llm_agent: '#38bdf8',
  planner: '#a78bfa',
  evaluator: '#34d399',
  tool: '#fbbf24',
}

export function AgentFlowCanvas({
  studyId,
  selectedRound,
  onSelectedRoundChange,
  totalRounds,
}: AgentFlowCanvasProps) {
  const [graph, setGraph] = useState<StudyGraphResponse | null>(null)
  const [agentOutputs, setAgentOutputs] = useState<StudyRoundAgentOutputsResponse['agent_outputs']>({})
  const [loadingGraph, setLoadingGraph] = useState(false)
  const [, setLoadingOutputs] = useState(false)
  const [selectedNode, setSelectedNode] = useState<(DAGNodeData & { id: string }) | null>(null)

  // Load the study graph (cached in the SSE useStudyStore.current).
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      setLoadingGraph(true)
      try {
        const r = await api.study.graph(studyId)
        if (!cancelled) setGraph(r)
      } catch {
        if (!cancelled) setGraph(null)
      } finally {
        if (!cancelled) setLoadingGraph(false)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [studyId])

// Load agent outputs for the selected round (with stale-response guard).
  useEffect(() => {
    if (!studyId || selectedRound < 1) {
      setAgentOutputs({})
      return
    }
    let cancelled = false
    const run = async () => {
      setLoadingOutputs(true)
      try {
        const r = await api.study.roundAgentOutputs(studyId, selectedRound)
        if (!cancelled) setAgentOutputs(r.agent_outputs ?? {})
      } catch {
        if (!cancelled) setAgentOutputs({})
      } finally {
        if (!cancelled) setLoadingOutputs(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [studyId, selectedRound])

  // Compute per-node status from agent_outputs.
  const nodeStatuses = useMemo(() => {
    const out: Record<string, 'completed' | 'error' | 'pending' | 'running'> = {}
    if (!graph) return out
    for (const n of graph.graph.nodes) {
      const data = agentOutputs[n.id]
      if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
        out[n.id] = 'pending'
      } else if (typeof data === 'object' && 'error' in data && (data as { error: unknown }).error) {
        out[n.id] = 'error'
      } else {
        out[n.id] = 'completed'
      }
    }
    return out
  }, [graph, agentOutputs])

  // Build the layouted nodes / edges via dagre.
  const { layoutedNodes, layoutedEdges } = useMemo(() => {
    if (!graph) return { layoutedNodes: [], layoutedEdges: [] }
    const rawNodes = graph.graph.nodes.map((n) => ({
      id: n.id,
      label: n.label || n.id,
      type: n.type,
      enabled: n.enabled !== false,
      agentColor: AGENT_COLORS[n.type] ?? '#64748b',
      status: nodeStatuses[n.id] ?? 'pending',
      nodeType: 'agent',
    }))
    const rawEdges = graph.graph.edges.map((e) => ({
      source: e.source,
      target: e.target,
    }))
    const layouted = layoutWithDagre(rawNodes, rawEdges, {
      nodeWidth: 180,
      nodeHeight: 80,
      nodeType: 'dagNode',
      edgeType: 'dagEdge',
    })
    return {
      layoutedNodes: layouted.nodes as Node[],
      layoutedEdges: layouted.edges as Edge[],
    }
  }, [graph, nodeStatuses])

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  useEffect(() => {
    setNodes(layoutedNodes)
    setEdges(layoutedEdges)
  }, [layoutedNodes, layoutedEdges, setNodes, setEdges])

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode(node.data as DAGNodeData & { id: string })
  }, [])
  const onPaneClick = useCallback(() => setSelectedNode(null), [])

  const nodeTypes: NodeTypes = useMemo(() => ({ dagNode: DAGNode }), [])
  const edgeTypes: EdgeTypes = useMemo(() => ({ dagEdge: DAGEdge }), [])

  const completedCount = Object.values(nodeStatuses).filter((s) => s === 'completed').length
  const errorCount = Object.values(nodeStatuses).filter((s) => s === 'error').length
  const totalNodes = graph?.graph.nodes.length ?? 0
  const progress = totalNodes > 0 ? Math.round((completedCount / totalNodes) * 100) : 0

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 shadow-soft overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <GitBranch className="h-3 w-3 text-primary-400" />
          Agent 流水线
          {graph?.persisted === false && (
            <span className="ml-1 rounded-full bg-amber-900/40 px-1.5 py-0.5 text-[9px] text-amber-300">
              fallback
            </span>
          )}
          {loadingGraph && (
            <div className="ml-2 h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
          )}
        </div>
        <RoundPicker
          currentRound={selectedRound}
          totalRounds={totalRounds}
          onChange={onSelectedRoundChange}
        />
      </div>

      {/* ReactFlow Canvas */}
      <div className="h-[360px]">
        {!graph || layoutedNodes.length === 0 ? (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">
            {loadingGraph ? '加载图中…' : '图未加载'}
          </div>
        ) : (
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
            fitViewOptions={{ padding: 0.15 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.3}
            maxZoom={2}
          >
            <Background variant={BackgroundVariant.Dots} gap={26} size={1.2} color="var(--canvas-grid)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        )}
      </div>

      {/* Progress bar */}
      {graph && totalNodes > 0 && (
        <div className="border-t border-slate-800 px-4 py-2">
          <DAGProgressBar
            progress={progress}
            completed={completedCount}
            total={totalNodes}
            error={errorCount}
          />
        </div>
      )}

      {/* Detail modal */}
      {selectedNode && (
        <AgentNodeDetail
          agentId={selectedNode.id}
          studyId={studyId}
          currentRound={selectedRound}
          onClose={() => setSelectedNode(null)}
        />
      )}
    </div>
  )
}