// FlowCard — 9-node stepper: status rendering (done/running/pending),
// connector styles, progress bar and header info.

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FlowCard, type FlowNodeData } from '../components/study/FlowCard'

vi.mock('lucide-react', () => {
  const Stub = () => null
  return { Activity: Stub }
})

function nodes(...overrides: Array<Partial<FlowNodeData>>): FlowNodeData[] {
  const base = ['researcher', 'backtest', 'risk_ctrl', 'attribution'].map((id) => ({
    id,
    label: id,
    status: 'pending' as const,
  }))
  return base.map((n, i) => ({ ...n, ...(overrides[i] ?? {}) }))
}

describe('FlowCard', () => {
  it('renders the header with round and total rounds', () => {
    render(<FlowCard nodes={nodes()} currentRound={2} totalRounds={5} />)
    expect(screen.getByText('当前流程 · Round 2')).toBeInTheDocument()
    expect(screen.getByText('共 5 轮')).toBeInTheDocument()
  })

  it('renders every node label', () => {
    render(<FlowCard nodes={nodes()} currentRound={1} />)
    for (const n of ['researcher', 'backtest', 'risk_ctrl', 'attribution']) {
      expect(screen.getByText(n)).toBeInTheDocument()
    }
  })

  it('marks done nodes with a checkmark and running nodes with pulse', () => {
    const { container } = render(
      <FlowCard
        nodes={nodes(
          { status: 'done' },
          { status: 'running' },
          { status: 'pending' },
        )}
        currentRound={1}
      />
    )
    expect(screen.getAllByText('✓').length).toBe(1)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThanOrEqual(1)
  })

  it('draws a solid connector before a done node and dashed before pending', () => {
    const { container } = render(
      <FlowCard
        nodes={nodes({ status: 'pending' }, { status: 'done' })}
        currentRound={1}
      />
    )
    expect(container.querySelector('[class*="bg-emerald-500"]')).toBeTruthy()
    expect(container.querySelector('.border-dashed')).toBeTruthy()
  })

  it('computes the progress bar from done count', () => {
    render(
      <FlowCard
        nodes={nodes({ status: 'done' }, { status: 'done' })}
        currentRound={1}
      />
    )
    expect(screen.getByText('2/4 步骤')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
  })

  it('shows 0% progress with all pending nodes', () => {
    render(<FlowCard nodes={nodes()} currentRound={1} />)
    expect(screen.getByText('0/4 步骤')).toBeInTheDocument()
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})
