// DAGNodeDetail — right-side slide-out panel that shows a workflow
// node's metadata. Verifies header + close button + conditional
// sections for agent/type/prompt/conditions.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DAGNodeDetail } from '../components/workflow/DAGNodeDetail'
import type { DAGNodeData } from '../components/workflow/DAGNode'

vi.mock('lucide-react', () => {
  const Stub = () => null
  return { X: Stub, Bot: Stub, Clock: Stub, Wrench: Stub, FileText: Stub, Zap: Stub }
})

vi.mock('../utils/status', () => ({
  statusBadgeClass: (s: string) => `status-${s}`,
  statusLabel: (s: string) => `STATUS:${s}`,
}))

function node(overrides: Partial<DAGNodeData> = {}): DAGNodeData & { id: string } {
  return {
    id: 'researcher',
    label: 'Researcher Agent',
    status: 'running',
    agentName: 'researcher_agent',
    agentColor: '#0ea5e9',
    type: 'llm-call',
    ...overrides,
  }
}

describe('DAGNodeDetail', () => {
  it('renders the node label and closes on X click', () => {
    const onClose = vi.fn()
    const { container } = render(<DAGNodeDetail node={node()} onClose={onClose} />)
    expect(screen.getByText('Researcher Agent')).toBeInTheDocument()
    // The X icon is mocked; grab the only header button directly.
    const closeBtn = container.querySelector('button')!
    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders the status badge via statusLabel/statusBadgeClass', () => {
    render(<DAGNodeDetail node={node({ status: 'completed' })} onClose={() => {}} />)
    expect(screen.getByText('STATUS:completed')).toBeInTheDocument()
  })

  it('renders agent/type sections when provided', () => {
    render(<DAGNodeDetail node={node()} onClose={() => {}} />)
    expect(screen.getByText('researcher_agent')).toBeInTheDocument()
    expect(screen.getByText('llm-call')).toBeInTheDocument()
  })

  it('omits agent/type sections when not provided', () => {
    const { queryByText } = render(
      <DAGNodeDetail node={node({ agentName: undefined, type: undefined })} onClose={() => {}} />
    )
    // "Agent" is a section title; query the agent value via the absence of value.
    expect(queryByText('llm-call')).toBeNull()
  })

  it('renders prompt and conditions when present in extras', () => {
    const n = node({
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      prompt: 'Find alpha factors',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      conditions: { market: 'bull' } as any,
    }) as DAGNodeData & { id: string; prompt: string; conditions: Record<string, unknown> }
    render(<DAGNodeDetail node={n} onClose={() => {}} />)
    expect(screen.getByText('Find alpha factors')).toBeInTheDocument()
    expect(screen.getByText(/market/)).toBeInTheDocument()
  })
})