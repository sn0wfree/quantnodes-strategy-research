import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'
import { DAGNode } from '../components/workflow/DAGNode'
import type { DAGNodeData } from '../components/workflow/DAGNode'

const mockNodeData: DAGNodeData = {
  label: 'Test Node',
  status: 'running',
  agentColor: '#3b82f6',
  agentName: 'TestAgent',
}

function renderWithReactFlow(nodeData: DAGNodeData, selected = false) {
  return render(
    <ReactFlowProvider>
      <DAGNode
        id="node-1"
        data={nodeData as any}
        selected={selected}
      />
    </ReactFlowProvider>
  )
}

describe('DAGNode', () => {
  it('renders label', () => {
    renderWithReactFlow(mockNodeData)
    expect(screen.getByText('Test Node')).toBeInTheDocument()
  })

  it('renders agent name', () => {
    renderWithReactFlow(mockNodeData)
    expect(screen.getByText('TestAgent')).toBeInTheDocument()
  })

  it('applies status border color', () => {
    const { container } = renderWithReactFlow(mockNodeData)
    const node = container.querySelector('.border-blue-500')
    expect(node).toBeInTheDocument()
  })

  it('renders pending status', () => {
    renderWithReactFlow({ ...mockNodeData, status: 'pending' })
    expect(screen.getByText('等待中')).toBeInTheDocument()
  })

  it('renders completed status', () => {
    renderWithReactFlow({ ...mockNodeData, status: 'completed' })
    expect(screen.getByText('已完成')).toBeInTheDocument()
  })

  it('renders failed status', () => {
    renderWithReactFlow({ ...mockNodeData, status: 'failed' })
    expect(screen.getByText('失败')).toBeInTheDocument()
  })
})