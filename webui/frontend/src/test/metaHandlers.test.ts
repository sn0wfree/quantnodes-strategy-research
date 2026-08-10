// hooks/sse/metaHandlers — goal_updated / goal_evidence_added /
// goal_completed / session_meta_updated. The goalUpdated handler also
// triggers a workflow sync, which we mock out here.

import { describe, it, expect, beforeEach, vi } from 'vitest'

const mockWorkflowStore = {
  dagNodes: [] as unknown[],
  getState: vi.fn(),
}
const mockSessionStore = {
  loadSessionState: vi.fn().mockResolvedValue(undefined),
  getState: vi.fn(),
}

vi.mock('../../stores/workflow', () => ({
  useWorkflowStore: {
    getState: () => mockWorkflowStore,
  },
}))

vi.mock('../../stores/session', () => ({
  useSessionStore: {
    getState: () => ({
      loadSessionState: mockSessionStore.loadSessionState,
    }),
  },
}))

import { useGoalStore } from '../stores/goal'
import {
  goalUpdated,
  sessionMetaUpdated,
} from '../hooks/sse/metaHandlers'

beforeEach(() => {
  vi.clearAllMocks()
  mockWorkflowStore.dagNodes = []
  mockSessionStore.loadSessionState.mockResolvedValue(undefined)
  useGoalStore.setState({ currentGoal: null })
})

describe('goalUpdated', () => {
  it('replaces currentGoal with the snapshot from the event', () => {
    goalUpdated(
      {
        goal_id: 'g-1',
        session_id: 'sess-1',
        status: 'active',
        objective: 'find alpha',
        progress_percent: 30,
        criteria: [{ criterion_id: 'c1', text: 'Sharpe > 1', status: 'pending', required: true }],
        evidence_count: 2,
      },
      { setGoal: useGoalStore.getState().setGoal } as never
    )
    const g = useGoalStore.getState().currentGoal
    expect(g?.goal_id).toBe('g-1')
    expect(g?.objective).toBe('find alpha')
    expect(g?.progress_percent).toBe(30)
    expect(g?.evidence_count).toBe(2)
  })

  it('ignores events without a goal_id', () => {
    goalUpdated(
      { objective: 'x' },
      { setGoal: useGoalStore.getState().setGoal } as never
    )
    expect(useGoalStore.getState().currentGoal).toBeNull()
  })

  it('skips the workflow sync when the DAG is already populated', async () => {
    mockWorkflowStore.dagNodes = [{ id: 'a' }]
    goalUpdated(
      {
        goal_id: 'g-1',
        session_id: 'sess-1',
      },
      { setGoal: useGoalStore.getState().setGoal } as never
    )
    // Give the dynamic import a tick.
    await new Promise((r) => setTimeout(r, 10))
    expect(mockSessionStore.loadSessionState).not.toHaveBeenCalled()
  })
})

describe('sessionMetaUpdated', () => {
  it('patches only the provided fields', () => {
    const patchSessionMeta = vi.fn()
    sessionMetaUpdated(
      {
        session_id: 'sess-1',
        title: 'Sharpe research',
        starred: true,
      },
      { patchSessionMeta } as never
    )
    expect(patchSessionMeta).toHaveBeenCalledWith('sess-1', {
      title: 'Sharpe research',
      starred: true,
    })
  })

  it('no-ops without session_id', () => {
    const patchSessionMeta = vi.fn()
    sessionMetaUpdated({ title: 'x' }, { patchSessionMeta } as never)
    expect(patchSessionMeta).not.toHaveBeenCalled()
  })

  it('passes through tags and archived', () => {
    const patchSessionMeta = vi.fn()
    sessionMetaUpdated(
      {
        session_id: 'sess-2',
        tags: ['strategy', 'urgent'],
        archived: true,
        message_count: 12,
      },
      { patchSessionMeta } as never
    )
    expect(patchSessionMeta).toHaveBeenCalledWith('sess-2', {
      tags: ['strategy', 'urgent'],
      archived: true,
      message_count: 12,
    })
  })
})