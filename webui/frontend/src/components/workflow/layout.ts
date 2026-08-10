import dagre from 'dagre'

export interface LayoutNodeData {
  id?: string
  [key: string]: unknown
}

export interface LayoutEdgeData {
  source: string
  target: string
  [key: string]: unknown
}

// dagre layout helper: rankdir LR, shared by read-only DAG viewer
// and the workflow editor's "自动布局" action.
export function layoutWithDagre(
  rawNodes: LayoutNodeData[],
  rawEdges: LayoutEdgeData[],
  options?: { nodeWidth?: number; nodeHeight?: number; nodeType?: string; edgeType?: string }
): { nodes: Array<{ id: string; position: { x: number; y: number }; data: Record<string, unknown>; type?: string }>; edges: Array<{ id: string; source: string; target: string; type?: string; data?: { animated: boolean } }> } {
  const { nodeWidth = 180, nodeHeight = 70, nodeType = 'dagNode', edgeType = 'dagEdge' } = options ?? {}
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 120 })

  rawNodes.forEach((n) => {
    g.setNode(n.id as string, { width: nodeWidth, height: nodeHeight })
  })
  rawEdges.forEach((e) => {
    g.setEdge(e.source, e.target)
  })

  dagre.layout(g)

  const nodes: Array<{ id: string; position: { x: number; y: number }; data: Record<string, unknown>; type?: string }> = rawNodes.map((n) => {
    const pos = g.node(n.id as string)
    return {
      id: n.id as string,
      position: { x: pos.x - nodeWidth / 2, y: pos.y - nodeHeight / 2 },
      data: n as unknown as Record<string, unknown>,
      type: nodeType,
    }
  })

  const edges: Array<{ id: string; source: string; target: string; type?: string; data?: { animated: boolean } }> = rawEdges.map((e, i) => ({
    id: `e-${i}`,
    source: e.source,
    target: e.target,
    type: edgeType,
    data: {
      animated: rawNodes.find((n) => n.id === e.target)?.status === 'running',
    },
  }))

  return { nodes, edges }
}
