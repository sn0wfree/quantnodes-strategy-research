// DAGProgressBar — progress bar width cap at 100, percent rounding,
// elapsed time formatting (<60s vs >=60s).

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DAGProgressBar } from '../components/workflow/DAGProgressBar'

describe('DAGProgressBar', () => {
  it('renders completed/total and rounded percent', () => {
    render(<DAGProgressBar progress={42} completed={4} total={10} />)
    expect(screen.getByText('4/10')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('rounds fractional progress to nearest int', () => {
    render(<DAGProgressBar progress={42.7} completed={4} total={10} />)
    expect(screen.getByText('43%')).toBeInTheDocument()
  })

  it('caps the bar width at 100% even if progress > 100', () => {
    const { container } = render(<DAGProgressBar progress={150} completed={5} total={5} />)
    const bar = container.querySelector('.bg-primary-500') as HTMLElement
    expect(bar.style.width).toBe('100%')
  })

  it('formats elapsed seconds when under a minute', () => {
    render(<DAGProgressBar progress={10} completed={1} total={10} elapsed={42} />)
    expect(screen.getByText('42s')).toBeInTheDocument()
  })

  it('formats elapsed minutes + seconds when >= 60s', () => {
    render(<DAGProgressBar progress={10} completed={1} total={10} elapsed={125} />)
    expect(screen.getByText('2m 5s')).toBeInTheDocument()
  })

  it('omits elapsed text when not provided', () => {
    render(<DAGProgressBar progress={10} completed={1} total={10} />)
    expect(screen.queryByText(/\d+s/)).toBeNull()
    expect(screen.queryByText(/\d+m/)).toBeNull()
  })
})