import { describe, it, expect, beforeEach } from 'vitest'
import { useWorkflowStore } from '../stores/workflow'

describe('useWorkflowStore', () => {
  beforeEach(() => {
    useWorkflowStore.setState({
      presets: [],
      currentPresetId: null,
      dagNodes: [],
      dagEdges: [],
      executionProgress: 0,
    })
  })

  it('setPresets replaces list', () => {
    useWorkflowStore.getState().setPresets([
      { id: 'p1', name: 'Preset 1', created_at: 1 },
      { id: 'p2', name: 'Preset 2', created_at: 2 },
    ])
    expect(useWorkflowStore.getState().presets.length).toBe(2)
  })

  it('setCurrentPreset sets and clears', () => {
    useWorkflowStore.getState().setCurrentPreset('p1')
    expect(useWorkflowStore.getState().currentPresetId).toBe('p1')

    useWorkflowStore.getState().setCurrentPreset(null)
    expect(useWorkflowStore.getState().currentPresetId).toBeNull()
  })

  it('setDAG sets nodes + edges atomically', () => {
    useWorkflowStore.getState().setDAG(
      [{ id: 'n1', label: 'Plan', status: 'pending' }],
      [{ id: 'e1', source: 'n1', target: 'n2' }],
    )
    const state = useWorkflowStore.getState()
    expect(state.dagNodes.length).toBe(1)
    expect(state.dagEdges.length).toBe(1)
  })

  it('updateNodeStatus only changes matching node', () => {
    useWorkflowStore.setState({
      dagNodes: [
        { id: 'n1', label: 'Plan', status: 'pending' },
        { id: 'n2', label: 'Execute', status: 'pending' },
      ],
      dagEdges: [],
      presets: [],
      currentPresetId: null,
      executionProgress: 0,
    })
    useWorkflowStore.getState().updateNodeStatus('n1', 'running')
    const nodes = useWorkflowStore.getState().dagNodes
    expect(nodes[0].status).toBe('running')
    expect(nodes[1].status).toBe('pending')
  })

  it('updateNodeStatus with unknown id is no-op', () => {
    useWorkflowStore.setState({
      dagNodes: [{ id: 'n1', label: 'Plan', status: 'pending' }],
      dagEdges: [],
      presets: [],
      currentPresetId: null,
      executionProgress: 0,
    })
    useWorkflowStore.getState().updateNodeStatus('n999', 'running')
    expect(useWorkflowStore.getState().dagNodes[0].status).toBe('pending')
  })

  it('setExecutionProgress stores percentage', () => {
    useWorkflowStore.getState().setExecutionProgress(0.5)
    expect(useWorkflowStore.getState().executionProgress).toBe(0.5)

    useWorkflowStore.getState().setExecutionProgress(1)
    expect(useWorkflowStore.getState().executionProgress).toBe(1)
  })
})