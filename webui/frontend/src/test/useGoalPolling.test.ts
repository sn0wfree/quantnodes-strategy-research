// useGoalPolling — polls /api/goal/status while the panel is active.

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useGoalPolling } from '../hooks/useGoalPolling'

const mockGetStatus = vi.fn()
vi.mock('../api/client', () => ({
  api: {
    goal: { getStatus: (...args: unknown[]) => mockGetStatus(...args) },
  },
}))

let mockCurrentSessionId: string | null = 'sess-1'
vi.mock('../stores/session', () => ({
  useSessionStore: (selector: (s: { currentSessionId: string | null }) => unknown) =>
    selector({ currentSessionId: mockCurrentSessionId }),
}))

import { useGoalStore } from '../stores/goal'

beforeEach(() => {
  vi.clearAllMocks()
  mockCurrentSessionId = 'sess-1'
  useGoalStore.setState({ currentGoal: null })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useGoalPolling', () => {
  it('does nothing when inactive', async () => {
    renderHook(() => useGoalPolling(false))
    await new Promise((r) => setTimeout(r, 50))
    expect(mockGetStatus).not.toHaveBeenCalled()
  })

  it('does nothing without a current session', async () => {
    mockCurrentSessionId = null
    renderHook(() => useGoalPolling(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(mockGetStatus).not.toHaveBeenCalled()
  })

  it('polls and stores the goal snapshot on success', async () => {
    mockGetStatus.mockResolvedValue({
      status: 'ok',
      goal_id: 'g-1',
      session_id: 'sess-1',
      goal_status: 'active',
      objective: 'find alpha',
      progress_percent: 50,
      criteria: [{ criterion_id: 'c-1', text: 'Sharpe > 1', status: 'pending', required: true }],
      evidence_count: 3,
    })
    renderHook(() => useGoalPolling(true))
    // Flush the initial poll() call.
    await act(async () => {
      await Promise.resolve()
    })
    const g = useGoalStore.getState().currentGoal!
    expect(g.goal_id).toBe('g-1')
    expect(g.progress_percent).toBe(50)
    expect(g.criteria).toHaveLength(1)
    expect(g.evidence_count).toBe(3)
  })

  it('clears the goal when the server reports no_goal', async () => {
    useGoalStore.getState().setGoal({
      goal_id: 'g-1',
      session_id: 'sess-1',
      status: 'active',
      objective: 'x',
      progress_percent: 0,
      criteria: [],
      evidence_count: 0,
    })
    mockGetStatus.mockResolvedValue({ status: 'no_goal' })
    renderHook(() => useGoalPolling(true))
    await act(async () => {
      await Promise.resolve()
    })
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

  it('keeps the last known state on transient API failure', async () => {
    mockGetStatus.mockRejectedValue(new Error('boom') as never)
    useGoalStore.getState().setGoal({
      goal_id: 'g-prior', session_id: 'sess-1', status: 'active',
      objective: 'x', progress_percent: 0, criteria: [], evidence_count: 0,
    })
    renderHook(() => useGoalPolling(true))
    await act(async () => {
      await Promise.resolve()
    })
    // Prior state is preserved when polling fails.
    expect(useGoalStore.getState().currentGoal?.goal_id).toBe('g-prior')
  })

  it('polls repeatedly on an interval and unmount stops the timer', async () => {
    vi.useFakeTimers()
    mockGetStatus.mockResolvedValue({
      status: 'ok',
      goal_id: 'g-1',
      progress_percent: 10,
      criteria: [],
      evidence_count: 0,
    } as never)
    const { unmount } = renderHook(() => useGoalPolling(true))
    // Initial poll + 3 ticks of the interval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
    })
    const callsBefore = mockGetStatus.mock.calls.length
    expect(callsBefore).toBeGreaterThan(1)
    unmount()
    // No further polls after unmount.
    const callsAtUnmount = mockGetStatus.mock.calls.length
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
    })
    expect(mockGetStatus.mock.calls.length).toBe(callsAtUnmount)
  })
})

// Exported from the module for the timer test above.
const POLL_INTERVAL_MS = 3000