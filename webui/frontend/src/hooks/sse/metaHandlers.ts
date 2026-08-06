import type { Goal } from '../../stores/goal'
import type { SSEHandler } from './types'

/** Backend goal_updated event payload (service.py / chat.py). */
export interface GoalUpdatedEvent {
  goal_id?: string
  session_id?: string
  status?: string
  objective?: string
  progress_percent?: number
  criteria?: unknown[]
  evidence_count?: number
}

/** Backend goal_evidence_added event payload. */
export interface GoalEvidenceAddedEvent {
  goal_id?: string
  progress_percent?: number
}

/** Backend goal_completed event payload. */
export interface GoalCompletedEvent {
  goal_id?: string
  status?: string
  recap?: string
}

/**
 * Goal SSE handlers — wired to backend goal_* events emitted from
 * service.py (_maybe_emit_goal_event) and chat.py (_emit_goal_sse_event).
 *
 * goalUpdated also triggers loadSessionState to sync the workflow store
 * (DAG panel) when the workflow is empty, so the right panel reflects
 * the goal immediately after creation.
 */
export const goalUpdated: SSEHandler = (data, { setGoal }) => {
  const goalData = data as GoalUpdatedEvent
  if (!goalData.goal_id) return

  setGoal({
    goal_id: goalData.goal_id,
    session_id: goalData.session_id || '',
    status: goalData.status || 'active',
    objective: goalData.objective || '',
    progress_percent: goalData.progress_percent || 0,
    criteria: (goalData.criteria ?? []) as Goal['criteria'],
    evidence_count: goalData.evidence_count || 0,
  })

  // Trigger loadSessionState to sync workflow store (DAG panel).
  // Only when workflow is empty to avoid overwriting an active workflow.
  _maybeSyncWorkflow(goalData.session_id)
}

export const goalEvidenceAdded: SSEHandler = (data, { updateGoal }) => {
  const evData = data as GoalEvidenceAddedEvent
  updateGoal((g) => {
    g.evidence_count = (g.evidence_count || 0) + 1
    if (evData.progress_percent !== undefined) {
      g.progress_percent = evData.progress_percent
    }
  })
}

export const goalCompleted: SSEHandler = (data, { updateGoal }) => {
  const compData = data as GoalCompletedEvent
  updateGoal((g) => {
    g.status = compData.status || 'complete'
    if (compData.recap) g.recap = compData.recap
  })
}

/**
 * session_meta_updated: server-side session metadata refresh (e.g.
 * auto-title after first message). Patches the in-memory session list.
 */
export const sessionMetaUpdated: SSEHandler = (data, { patchSessionMeta }) => {
  const { session_id, title, message_count, starred, tags, archived } = data as {
    session_id: string
    title?: string
    message_count?: number
    starred?: boolean
    tags?: string[]
    archived?: boolean
  }
  if (!session_id) return
  const patch: Record<string, unknown> = {}
  if (title !== undefined) patch.title = title
  if (message_count !== undefined) patch.message_count = message_count
  if (starred !== undefined) patch.starred = starred
  if (tags !== undefined) patch.tags = tags
  if (archived !== undefined) patch.archived = archived
  patchSessionMeta(session_id, patch)
}

// ── Internal helpers ─────────────────────────────────────────────

let _syncInFlight = false

/**
 * Trigger loadSessionState to populate the workflow store (DAG panel)
 * when it's currently empty.  Called after goal_updated events so the
 * right panel shows the DAG alongside the goal immediately.
 *
 * Debounced: if a sync is already in flight, skip.
 */
function _maybeSyncWorkflow(sessionId: string | null | undefined) {
  if (!sessionId || _syncInFlight) return

  // Lazy imports to avoid circular deps (these are zustand singletons)
  import('../../stores/workflow').then(({ useWorkflowStore }) => {
    const wfState = useWorkflowStore.getState()
    if (wfState.dagNodes.length > 0) return  // workflow already populated

    _syncInFlight = true
    import('../../stores/session').then(({ useSessionStore }) => {
      useSessionStore
        .getState()
        .loadSessionState(sessionId)
        .catch(() => {})
        .finally(() => {
          _syncInFlight = false
        })
    }).catch(() => { _syncInFlight = false })
  }).catch(() => {})
}
