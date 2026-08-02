import { useEffect, useRef } from 'react'
import { api } from '../api/client'
import { useGoalStore } from '../stores/goal'
import { useSessionStore } from '../stores/session'

const POLL_INTERVAL_MS = 3000

/**
 * Poll /api/goal/status for the active session while the Goal panel is
 * open, so the Goal tab reflects live goal state without relying on
 * backend goal_* SSE events (which are not emitted today — see
 * hooks/sse/metaHandlers.ts TODO).
 *
 * The backend returns the full snapshot (criteria / evidence_count /
 * progress_percent) from GoalStore.get_current_snapshot, so a poll is
 * a single source of truth covering all goal mutation paths (chat
 * tools, REST API, workflows).
 */
export function useGoalPolling(active: boolean) {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const setGoal = useGoalStore((s) => s.setGoal)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!active || !currentSessionId) return

    const poll = async () => {
      try {
        const res = await api.goal.getStatus(currentSessionId)
        if (!res || res.status === 'no_goal' || !res.goal_id) {
          setGoal(null)
          return
        }
        setGoal({
          goal_id: res.goal_id,
          session_id: res.session_id || currentSessionId,
          status: res.goal_status || 'active',
          objective: res.objective || '',
          progress_percent: res.progress_percent ?? 0,
          criteria: (res.criteria || []).map((c) => ({
            criterion_id: c.criterion_id,
            text: c.text,
            status: c.status,
            required: c.required,
            evidence_count: c.evidence_count ?? 0,
          })),
          evidence_count: res.evidence_count ?? 0,
        })
      } catch {
        // transient poll failure — keep last known state, retry next tick
      }
    }

    poll()
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [active, currentSessionId, setGoal])
}
