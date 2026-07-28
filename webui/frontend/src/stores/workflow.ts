import { create } from 'zustand'

export interface WorkflowPreset {
  id: string
  name: string
  description?: string
  created_at: number
}

export interface DAGNode {
  id: string
  label: string
  type?: string
  status?: 'pending' | 'running' | 'completed' | 'failed'
}

export interface DAGEdge {
  id: string
  source: string
  target: string
}

interface WorkflowState {
  presets: WorkflowPreset[]
  currentPresetId: string | null
  dagNodes: DAGNode[]
  dagEdges: DAGEdge[]
  executionProgress: number
  setPresets: (presets: WorkflowPreset[]) => void
  setCurrentPreset: (id: string | null) => void
  setDAG: (nodes: DAGNode[], edges: DAGEdge[]) => void
  updateNodeStatus: (id: string, status: DAGNode['status']) => void
  setExecutionProgress: (p: number) => void
}

export const useWorkflowStore = create<WorkflowState>()((set) => ({
  presets: [],
  currentPresetId: null,
  dagNodes: [],
  dagEdges: [],
  executionProgress: 0,
  setPresets: (presets) => set({ presets }),
  setCurrentPreset: (id) => set({ currentPresetId: id }),
  setDAG: (nodes, edges) => set({ dagNodes: nodes, dagEdges: edges }),
  updateNodeStatus: (id, status) =>
    set((state) => ({
      dagNodes: state.dagNodes.map((n) =>
        n.id === id ? { ...n, status } : n
      ),
    })),
  setExecutionProgress: (p) => set({ executionProgress: p }),
}))
