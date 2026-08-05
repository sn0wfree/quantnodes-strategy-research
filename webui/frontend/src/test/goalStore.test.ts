// goal store — covers setGoal/updateGoal/clearGoal and the
// structuredClone invariant on updateGoal (nested fields).

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

  it('updateGoal mutates a deep clone (does not leak into the previous state)', () => {
    const goal = fixture() as import('../stores/goal').Goal
    useGoalStore.getState().setGoal(goal)
    useGoalStore.getState().updateGoal((g) => {
      g.criteria[0].status = 'covered'
      g.progress_percent = 80
    })
    const after = useGoalStore.getState().currentGoal!
    expect(after.criteria[0].status).toBe('covered')
    expect(after.progress_percent).toBe(80)
    // The originally-passed goal object is unchanged (structuredClone).
    expect(goal.criteria[0].status).toBe('pending')
    expect(goal.progress_percent).toBe(30)
  })

  it('updateGoal is a no-op when there is no current goal', () => {
    let invoked = false
    useGoalStore.getState().updateGoal(() => {
      invoked = true
    })
    expect(invoked).toBe(false)
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

  it('updateGoal isolates arrays so callers can append without leaking', () => {
    useGoalStore.getState().setGoal(fixture())
    useGoalStore.getState().updateGoal((g) => {
      g.criteria.push({ criterion_id: 'c-2', text: 'dd < 20', status: 'pending', required: true })
    })
    const after = useGoalStore.getState().currentGoal!
    expect(after.criteria).toHaveLength(2)
  })
})