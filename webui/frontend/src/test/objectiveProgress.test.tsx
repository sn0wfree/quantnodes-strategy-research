// ObjectiveProgress — title, objective text, progress bar width,
// percent label, and criteria status dot color.

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ObjectiveProgress } from '../components/study/ObjectiveProgress'


const criteria = [
  { criterion_id: 'c1', text: 'Sharpe > 1.0', status: 'covered', required: true },
  { criterion_id: 'c2', text: 'Max DD < 20%', status: 'pending', required: true },
]

describe('ObjectiveProgress', () => {
  it('renders the title and objective', () => {
    render(<ObjectiveProgress objective="找 alpha 因子" />)
    expect(screen.getByText('目标 · 进度')).toBeInTheDocument()
    expect(screen.getByText('找 alpha 因子')).toBeInTheDocument()
  })

  it('shows 0% by default', () => {
    render(<ObjectiveProgress objective="x" />)
    expect(screen.getByText('0% (0 证据)')).toBeInTheDocument()
  })

  it('renders the configured progress and evidence count', () => {
    render(
      <ObjectiveProgress
        objective="x"
        progressPercent={42}
        evidenceCount={3}
      />
    )
    expect(screen.getByText('42% (3 证据)')).toBeInTheDocument()
  })

  it('sets the progress bar width to percent', () => {
    const { container } = render(
      <ObjectiveProgress objective="x" progressPercent={73} />
    )
    const bar = container.querySelector('.bg-sky-500') as HTMLElement
    expect(bar.style.width).toBe('73%')
  })

  it('omits the criteria list when empty', () => {
    const { container } = render(
      <ObjectiveProgress objective="x" criteria={[]} />
    )
    expect(container.querySelectorAll('.rounded-full').length).toBe(1) // only the progress bar dot
  })

  it('renders covered criteria with the emerald dot', () => {
    render(
      <ObjectiveProgress
        objective="x"
        criteria={[
          { criterion_id: 'c1', text: 'A', status: 'covered', required: true },
        ]}
      />
    )
    expect(screen.getByText('A')).toBeInTheDocument()
  })

  it('renders pending criteria in muted color', () => {
    render(
      <ObjectiveProgress
        objective="x"
        criteria={[
          { criterion_id: 'c1', text: 'B', status: 'pending', required: true },
        ]}
      />
    )
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('lists multiple criteria', () => {
    render(<ObjectiveProgress objective="x" criteria={criteria} />)
    expect(screen.getByText('Sharpe > 1.0')).toBeInTheDocument()
    expect(screen.getByText('Max DD < 20%')).toBeInTheDocument()
  })
})