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
  goalEvidenceAdded,
  goalCompleted,
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

describe('goalEvidenceAdded', () => {
  it('bumps evidence_count and applies a fresh progress_percent', () => {
    useGoalStore.getState().setGoal({
      goal_id: 'g-1',
      session_id: 'sess-1',
      status: 'active',
      objective: 'x',
      progress_percent: 30,
      criteria: [],
      evidence_count: 2,
    })
    goalEvidenceAdded(
      { progress_percent: 60 },
      { updateGoal: useGoalStore.getState().updateGoal } as never
    )
    const g = useGoalStore.getState().currentGoal!
    expect(g.evidence_count).toBe(3)
    expect(g.progress_percent).toBe(60)
  })
})

describe('goalCompleted', () => {
  it('updates status to "complete" and stores the recap', () => {
    useGoalStore.getState().setGoal({
      goal_id: 'g-1', session_id: 'sess-1', status: 'active',
      objective: 'x', progress_percent: 0, criteria: [], evidence_count: 0,
    })
    goalCompleted(
      { status: 'complete', recap: 'sharpe 1.4 achieved' },
      { updateGoal: useGoalStore.getState().updateGoal } as never
    )
    const g = useGoalStore.getState().currentGoal!
    expect(g.status).toBe('complete')
    expect(g.recap).toBe('sharpe 1.4 achieved')
  })

  it('falls back to "complete" when status is absent', () => {
    useGoalStore.getState().setGoal({
      goal_id: 'g-1', session_id: 'sess-1', status: 'active',
      objective: 'x', progress_percent: 0, criteria: [], evidence_count: 0,
    })
    goalCompleted({}, { updateGoal: useGoalStore.getState().updateGoal } as never)
    expect(useGoalStore.getState().currentGoal!.status).toBe('complete')
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