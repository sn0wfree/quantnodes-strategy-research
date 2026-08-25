import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { RoundDetailDrawer } from '../components/study/RoundDetailDrawer'
import type { StudyRoundSummary } from '../api/client'

// vi.hoisted ensures these mocks are available when vi.mock() runs
// (vitest hoists vi.mock() to the top of the file, before top-level
// const declarations would otherwise be initialized).
const { roundDiffMock, roundManifestMock, roundArtifactsMock } = vi.hoisted(() => ({
  roundDiffMock: vi.fn().mockResolvedValue({
    diff: [],
    against_strategy: 'baseline',
    against_round: 0,
  }),
  roundManifestMock: vi.fn().mockResolvedValue({
    manifest: {
      round_num: 1,
      inherited_from: 'baseline',
      adopted_run: null,
      run_name: 'round_0001',
      hypothesis: { text: 'h', levers: [], predicted_affected: [] },
      levers: ['discover_local'],
      predicted_affected: ['calmar'],
      strategy_changes: null,
      metrics: { calmar: 0.6 },
      prev_metrics: null,
      baseline_metrics: null,
      verdict: { decision: 'keep', reason: '' },
      gates: null,
      budget: null,
    },
  }),
  roundArtifactsMock: vi.fn().mockResolvedValue({
    files: [],
    run_dir: 'study/study-1/rounds/round_0001',
  }),
}))

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      study: {
        ...actual.api.study,
        roundDiff: roundDiffMock,
        roundManifest: roundManifestMock,
        roundArtifacts: roundArtifactsMock,
      },
    },
  }
})

const roundFixture: StudyRoundSummary = {
  round_num: 1,
  run_name: 'round_0001',
  verdict: 'keep',
  metrics: { calmar: 0.6 },
  created_at: '',
}

describe('RoundDetailDrawer P1-4 baseline diff regression', () => {
  beforeEach(() => {
    roundDiffMock.mockClear()
    roundManifestMock.mockClear()
    roundArtifactsMock.mockClear()
  })

  it('calls roundDiff with against=0 on mount (no early return for default)', async () => {
    render(
      <RoundDetailDrawer
        studyId="study-1"
        round={roundFixture}
        onClose={() => {}}
      />
    )

    // diffAgainst defaults to 0 (vs baseline). Pre-fix the useEffect
    // had `if (diffAgainst === 0) return` which skipped the fetch —
    // roundDiff would never have been called.
    await waitFor(() => {
      expect(roundDiffMock).toHaveBeenCalledWith('study-1', 1, 0)
    })
  })

  it('diff against default option fetches baseline diff (not skipped)', async () => {
    render(
      <RoundDetailDrawer
        studyId="study-2"
        round={roundFixture}
        onClose={() => {}}
      />
    )

    // The fetch must happen (not skipped by the old early-return guard).
    // We assert on the call count being > 0 (any roundDiff call counts).
    await waitFor(() => {
      expect(roundDiffMock.mock.calls.length).toBeGreaterThan(0)
    })
  })
})