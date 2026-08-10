// goal store — setGoal / clearGoal. updateGoal was removed with the
// incremental goal SSE handlers (full-snapshot goal_updated events
// replace it — docs/goal-events-panel-link.md).

import { describe, it, expect, beforeEach } from 'vitest'
import { useGoalStore } from '../stores/goal'

function fixture(overrides: Partial<{
  goal_id: string
  criteria: { criterion_id: string; text: string; status: string; required: boolean }[]
}> = {}) {
  return {
    goal_id: 'g-1',
    session_id: 'sess-1',
    status: 'active',
    objective: 'find alpha',
    progress_percent: 30,
    criteria: [
      { criterion_id: 'c-1', text: 'Sharpe > 1', status: 'pending', required: true },
    ],
    evidence_count: 0,
    ...overrides,
  } as never
}

describe('useGoalStore', () => {
  beforeEach(() => {
    useGoalStore.setState({ currentGoal: null })
  })

  it('starts with no current goal', () => {
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

  it('replaces currentGoal via setGoal', () => {
    useGoalStore.getState().setGoal(fixture())
    expect(useGoalStore.getState().currentGoal?.goal_id).toBe('g-1')
  })

  it('clears currentGoal via clearGoal', () => {
    useGoalStore.getState().setGoal(fixture())
    useGoalStore.getState().clearGoal()
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

})