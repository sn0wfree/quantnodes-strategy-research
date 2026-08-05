// CriteriaList — progress summary + per-status icon rendering.
// Avoids asserting on icons by class; uses accessible roles/labels.

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { CriteriaList } from '../components/goal/CriteriaList'


const items = [
  { id: 'c1', description: 'Sharpe > 1.0', status: 'completed' as const, evidence_count: 2 },
  { id: 'c2', description: 'Max DD < 20%', status: 'in_progress' as const, evidence_count: 1 },
  { id: 'c3', description: '回测可复现', status: 'pending' as const, evidence_count: 0, agent_id: 'agent-abcdef1234' },
]

describe('CriteriaList', () => {
  it('renders 0% progress when no criteria are completed', () => {
    render(
      <CriteriaList
        criteria={[
          { id: 'c', description: 'd', status: 'pending', evidence_count: 0 },
        ]}
        totalCriteria={2}
      />
    )
    expect(screen.getByText('0/2 (0%)')).toBeInTheDocument()
  })

  it('computes percent from completed vs totalCriteria', () => {
    render(<CriteriaList criteria={items} totalCriteria={4} />)
    expect(screen.getByText('1/4 (25%)')).toBeInTheDocument()
  })

  it('shows 0% when totalCriteria is 0 (no division by zero)', () => {
    render(<CriteriaList criteria={[]} totalCriteria={0} />)
    expect(screen.getByText('0/0 (0%)')).toBeInTheDocument()
  })

  it('sets the progress bar width to percent', () => {
    const { container } = render(
      <CriteriaList
        criteria={[
          { id: 'c', description: 'd', status: 'completed', evidence_count: 0 },
        ]}
        totalCriteria={1}
      />
    )
    const bar = container.querySelector('.bg-primary-500') as HTMLElement
    expect(bar.style.width).toBe('100%')
  })

  it('renders each criterion description', () => {
    render(<CriteriaList criteria={items} totalCriteria={3} />)
    expect(screen.getByText('Sharpe > 1.0')).toBeInTheDocument()
    expect(screen.getByText('Max DD < 20%')).toBeInTheDocument()
    expect(screen.getByText('回测可复现')).toBeInTheDocument()
  })

  it('shows evidence count when > 0', () => {
    render(<CriteriaList criteria={items} totalCriteria={3} />)
    expect(screen.getByText('2 条证据')).toBeInTheDocument()
    expect(screen.getByText('1 条证据')).toBeInTheDocument()
  })

  it('hides evidence text for zero count', () => {
    const { queryByText } = render(
      <CriteriaList
        criteria={[
          { id: 'c', description: 'no evidence', status: 'pending', evidence_count: 0 },
        ]}
        totalCriteria={1}
      />
    )
    expect(queryByText(/条证据/)).toBeNull()
  })

  it('shows the agent id prefix when present', () => {
    render(<CriteriaList criteria={items} totalCriteria={3} />)
    expect(screen.getByText(/Agent: agent-ab/)).toBeInTheDocument()
  })
})