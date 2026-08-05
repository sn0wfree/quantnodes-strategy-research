// ScoreboardMini — empty state, precision color buckets, value formatting.

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreboardMini } from '../components/study/ScoreboardMini'
import type { LeverScoreSummary } from '../api/client'


function lever(overrides: Partial<LeverScoreSummary> = {}): LeverScoreSummary {
  return {
    lever: 'momentum',
    precision_mean: 0.6,
    attempts: 5,
    accepted: 3,
    reverted: 1,
    ...overrides,
  }
}

describe('ScoreboardMini', () => {
  it('renders the empty state when no levers', () => {
    render(<ScoreboardMini scoreboard={[]} />)
    expect(screen.getByText('Scoreboard')).toBeInTheDocument()
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

it('renders one row per lever with name, precision and accepted/attempts', () => {
    render(
      <ScoreboardMini
        scoreboard={[lever(), lever({ lever: 'reversal', precision_mean: 0.2, accepted: 0 })]}
      />
    )
    expect(screen.getByText('momentum')).toBeInTheDocument()
    expect(screen.getByText('reversal')).toBeInTheDocument()
    expect(screen.getByText('0.60')).toBeInTheDocument()
    expect(screen.getByText('0.20')).toBeInTheDocument()
  })

  it('uses emerald color for high precision (>= 0.7)', () => {
    const { container } = render(<ScoreboardMini scoreboard={[lever({ precision_mean: 0.9 })]} />)
    expect(container.querySelector('.bg-emerald-500')).toBeTruthy()
  })

  it('uses amber color for mid precision (0.5..0.7)', () => {
    const { container } = render(<ScoreboardMini scoreboard={[lever({ precision_mean: 0.6 })]} />)
    expect(container.querySelector('.bg-amber-500')).toBeTruthy()
  })

  it('uses rose color for low precision (< 0.5)', () => {
    const { container } = render(<ScoreboardMini scoreboard={[lever({ precision_mean: 0.2 })]} />)
    expect(container.querySelector('.bg-rose-500')).toBeTruthy()
  })

  it('sets the precision bar width to percent', () => {
    const { container } = render(<ScoreboardMini scoreboard={[lever({ precision_mean: 0.42 })]} />)
    const bar = container.querySelector('.bg-rose-500') as HTMLElement
    expect(bar.style.width).toBe('42%')
  })
})