// RoundDetailDrawer regression: rendering must survive a diff payload
// without `stats` (older persisted events / partial API responses) and
// a client whose roundDiff method is unavailable.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RoundDetailDrawer } from '../components/study/RoundDetailDrawer'
import { api } from '../api/client'
import type { StudyRoundDiffResponse } from '../api/client'

const roundDiffMock = vi.fn()

vi.mock('../api/client', () => ({
  api: {
    study: {
      roundManifest: vi.fn().mockResolvedValue(null),
      roundArtifacts: vi.fn().mockResolvedValue(null),
      roundSummaryMd: vi.fn().mockResolvedValue(null),
      roundDiff: (...args: unknown[]) => roundDiffMock(...args),
    },
  },
}))

vi.mock('lucide-react', async () => {
  const Stub = () => null
  const actual = await import('lucide-react')
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(actual)) out[key] = Stub
  return out
})

const baseRound = {
  round_num: 2,
  run_name: 'run_0002',
  metrics: { sharpe: 1.1 },
  verdict: 'keep',
  created_at: '2026-08-01T10:30:00',
} as never

function diffPayload(stats?: StudyRoundDiffResponse['stats']): StudyRoundDiffResponse {
  return {
    status: 'ok',
    study_id: 'st-1',
    round_a: 2,
    round_b: 0,
    diff: [{ line: 'x = 1', kind: 'add' }],
    ...(stats ? { stats } : {}),
  }
}

beforeEach(() => {
  roundDiffMock.mockReset()
  // restore in case a previous test detached the method
  ;(api.study as unknown as Record<string, unknown>).roundDiff = (
    ...args: unknown[]
  ) => roundDiffMock(...args)
})

describe('RoundDetailDrawer diff guards', () => {
  it('renders diff lines without crashing when stats is missing', async () => {
    roundDiffMock.mockResolvedValue(diffPayload())
    const { container } = render(
      <RoundDetailDrawer studyId="st-1" round={baseRound} onClose={() => {}} />
    )
    expect(await screen.findByText('+ x = 1')).toBeInTheDocument()
    expect(container.textContent).not.toContain('ctx')
  })

  it('renders stats counters when stats is present', async () => {
    roundDiffMock.mockResolvedValue(
      diffPayload({ adds: 3, dels: 1, context: 5 })
    )
    render(<RoundDetailDrawer studyId="st-1" round={baseRound} onClose={() => {}} />)
    expect(await screen.findByText(/3 \/ -1 \/ 5 ctx/)).toBeInTheDocument()
  })

  it('does not throw when roundDiff is unavailable on the client', () => {
    const broken = api.study as Record<string, unknown>
    broken.roundDiff = undefined
    expect(() =>
      render(
        <RoundDetailDrawer studyId="st-1" round={baseRound} onClose={() => {}} />
      )
    ).not.toThrow()
  })

  it('renders without crashing when artifacts payload lacks the artifacts array', async () => {
    roundDiffMock.mockResolvedValue(diffPayload())
    ;(api.study.roundArtifacts as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'ok',
    })
    render(<RoundDetailDrawer studyId="st-1" round={baseRound} onClose={() => {}} />)
    expect(await screen.findByText('+ x = 1')).toBeInTheDocument()
    expect(screen.queryByText(/产物 ·/)).not.toBeInTheDocument()
  })
})
