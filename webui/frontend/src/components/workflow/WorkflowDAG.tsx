import { useState, useCallback, useMemo, useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes,
  type EdgeTypes,
  BackgroundVariant,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { DAGNode, type DAGNodeData } from './DAGNode'
import { DAGEdge } from './DAGEdge'
import { DAGToolbar } from './DAGToolbar'
import { DAGProgressBar } from './DAGProgressBar'
import { DAGNodeDetail } from './DAGNodeDetail'
import { EmptyState } from '../common/EmptyState'
import { Workflow } from 'lucide-react'

// dagre layout helper
function layoutWithDagre(
  rawNodes: DAGNodeData[],
  rawEdges: { source: string; target: string }[]
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 120 })

  rawNodes.forEach((n) => {
    g.setNode(n.id as string, { width: 180, height: 70 })
  })
  rawEdges.forEach((e) => {
    g.setEdge(e.source, e.target)
  })

  dagre.layout(g)

  const nodes: Node[] = rawNodes.map((n) => {
    const pos = g.node(n.id as string)
    return {
      id: n.id as string,
      position: { x: pos.x - 90, y: pos.y - 35 },
      data: n as unknown as Record<string, unknown>,
      type: 'dagNode',
    }
  })

  const edges: Edge[] = rawEdges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    type: 'dagEdge',
    data: {
      animated: rawNodes.find((n) => n.id === e.target)?.status === 'running',
    },
  }))

  return { nodes, edges }
}

interface WorkflowDAGProps {
  workflowName?: string
  nodes?: DAGNodeData[]
  edges?: { source: string; target: string }[]
  status?: 'idle' | 'running' | 'paused' | 'completed' | 'failed'
  progress?: number
  completed?: number
  total?: number
  elapsed?: number
  onStart?: () => void
  onPause?: () => void
  onResume?: () => void
  onReset?: () => void
}

export function WorkflowDAG({
  workflowName = '未命名工作流',
  nodes: rawNodes = [],
  edges: rawEdges = [],
  status = 'idle',
  progress = 0,
  completed = 0,
  total = 0,
  elapsed,
  onStart,
  onPause,
  onResume,
  onReset,
}: WorkflowDAGProps) {
  const [selectedNode, setSelectedNode] = useState<(DAGNodeData & { id: string }) | null>(null)

  const { nodes: layoutNodes, edges: layoutEdges } = useMemo(
    () => layoutWithDagre(rawNodes, rawEdges),
    [rawNodes, rawEdges]
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)

  // Update nodes/edges when props change (effect, not memo — side
  // effects belong in useEffect; useMemo must stay pure).
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

  if (rawNodes.length === 0) {
    return (
      <EmptyState
        icon={<Workflow className="h-10 w-10" />}
        title="空工作流"
        description="添加节点后显示 DAG"
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      <DAGToolbar
        workflowName={workflowName}
        status={status}
        onStart={onStart}
        onPause={onPause}
        onResume={onResume}
        onReset={onReset}
      />
      <div className="relative flex-1 min-h-0">
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
          minZoom={0.3}
          maxZoom={2}
          defaultEdgeOptions={{ type: 'dagEdge' }}
          style={{ width: '100%', height: '100%' }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1e293b" />
          <Controls className="!bg-slate-800 !border-slate-700 !rounded-lg" />
          <MiniMap
            nodeColor={(n) => {
              const d = n.data as unknown as DAGNodeData
              return d.agentColor || '#475569'
            }}
            maskColor="rgba(0, 0, 0, 0.7)"
            className="!bg-slate-900 !border-slate-700"
          />
        </ReactFlow>

        {/* Node detail slide-out */}
        {selectedNode && (
          <DAGNodeDetail
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
      <DAGProgressBar
        progress={progress}
        completed={completed}
        total={total}
        elapsed={elapsed}
      />
    </div>
  )
}
