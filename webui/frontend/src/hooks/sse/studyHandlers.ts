import type { SSEHandler } from './types'
import { useStudyStore } from '../../stores/study'

/**
 * Study event handlers — keep the Study panel's progress view in sync
 * with the backend's ``study_*`` SSE events (emitted by StudyScheduler /
 * AutoresearchExecutor via EventStore → SSE). The StudyProgress poll
 * remains the authoritative fallback (3s) — these handlers just make
 * updates instant when the tab is open.
 *
 * Backend payloads (docs/study-longhorizon-plan.md §6):
 *   study_queued       {study_id, session_id, objective}
 *   study_started      {study_id, round}
 *   study_round        {study_id, round, run, metrics, verdict, agent_statuses}
 *   study_directives_consumed {study_id, round, consumed_ids, count}
 *   study_progress     {study_id, covered, total, percent}
 *   study_completed    {study_id, goal_id, metrics, round, recap}
 *   study_failed       {study_id, error, reason}
 *   study_budget_limited {study_id, used}
 *   study_paused       {study_id, round}
 *   study_resumed      {study_id, round}
 *   study_cancelled    {study_id}
 *   study_monitoring_started {study_id, interval_seconds}
 *   study_monitor_check {study_id, metrics, meets_targets, drift, drift_count}
 *   study_drift_detected {study_id, metrics, reason}
 */

const patch = (data: Record<string, unknown>) => {
  const cur = useStudyStore.getState().current
  // Start from the incoming payload, then layer the current snapshot
  // under it so status/objective/etc. survive partial events.
  const merged: Record<string, unknown> = { ...(cur ?? {}), ...data }
  const studyId = (data.study_id as string) ?? merged.study_id
  if (studyId) merged.study_id = studyId
  if (data.round !== undefined) merged.current_round = data.round as number
  if (data.metrics !== undefined) merged.last_metrics = data.metrics as Record<string, number>
  if (data.verdict !== undefined) merged.last_verdict = data.verdict as string
  if (data.error !== undefined) merged.last_error = data.error as string
  useStudyStore.getState().setCurrent(merged as never)
}

export const studyRound: SSEHandler = (data) => patch(data)

export const studyCompleted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'complete' })
}

export const studyFailed: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'error' })
}

export const studyBudgetLimited: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'budget_limited' })
}

export const studyPaused: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'paused' })
}

export const studyResumed: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'running' })
}

export const studyCancelled: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'cancelled' })
}

export const studyStarted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'running' })
}

export const studyQueued: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'queued' })
}

export const studyMonitoringStarted: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'monitoring' })
}

export const studyDriftDetected: SSEHandler = (data) => {
  patch({ ...data, execution_status: 'needs_refresh' })
}

export const studyMonitorCheck: SSEHandler = (data) => {
  patch(data)
}