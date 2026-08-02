import type { SSEHandler } from './types'

/**
 * Goal events — TODO(feature): dead chain end-to-end today. No backend
 * emitter produces goal_* events (verified: zero `emit(...goal...)`
 * calls in src/), so GoalTab/CriteriaList/GoalTimeline can only ever
 * render empty states. Planned wiring: the goal service emits these
 * events on start/evidence/complete
 * (docs/goal-workflow-design.md), or the frontend polls
 * /api/goal/status. The handlers below are kept ready for that event
 * contract.
 */
export const goalUpdated: SSEHandler = (data, { setGoal }) => {
  const goalData = data as any
  if (!goalData.goal_id) return
  setGoal({
    goal_id: goalData.goal_id,
    session_id: goalData.session_id || '',
    status: goalData.status || 'active',
    objective: goalData.objective || '',
    progress_percent: goalData.progress_percent || 0,
    criteria: goalData.criteria || [],
    evidence_count: goalData.evidence_count || 0,
  })
}

export const goalEvidenceAdded: SSEHandler = (data, { updateGoal }) => {
  const evData = data as any
  updateGoal((g) => {
    g.evidence_count = (g.evidence_count || 0) + 1
    if (evData.progress_percent !== undefined) {
      g.progress_percent = evData.progress_percent
    }
  })
}

export const goalCompleted: SSEHandler = (data, { updateGoal }) => {
  const compData = data as any
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