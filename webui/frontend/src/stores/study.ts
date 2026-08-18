import { create } from 'zustand'
import type { StudySummary, StudyStatusResponse } from '../api/client'

export interface AgentApprovalRequest {
  study_id: string
  role: string | null
  tool_hash: string
  window: number
  iteration: number
  timeout_s: number
  on_timeout: string
  message: string
  requested_at: number
}

export interface StudyState {
  /** Most recent status response for the active session. */
  current: StudyStatusResponse | null
  /** Cached list (used by the GoalsPanel history tab). */
  list: StudySummary[]
  /** True while a write is in flight (start/pause/...). */
  busy: boolean
  /** Last error surfaced to the UI (empty when none). */
  error: string
  /** Pending agent-loop approval gates, keyed by study_id+role+iteration. */
  agentApprovals: Record<string, AgentApprovalRequest>

  setCurrent: (s: StudyStatusResponse | null) => void
  setList: (rows: StudySummary[]) => void
  setBusy: (b: boolean) => void
  setError: (e: string) => void
  reset: () => void
  enqueueAgentApproval: (req: AgentApprovalRequest) => void
  resolveAgentApproval: (studyId: string, role: string | null, iter: number | undefined) => void
}

const approvalKey = (studyId: string, role: string | null, iter: number | undefined) =>
  `${studyId}::${role ?? ''}::${iter ?? 0}`

export const useStudyStore = create<StudyState>()((set) => ({
  current: null,
  list: [],
  busy: false,
  error: '',
  agentApprovals: {},
  setCurrent: (current) => set({ current }),
  setList: (list) => set({ list }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  reset: () => set({ current: null, list: [], busy: false, error: '', agentApprovals: {} }),
  enqueueAgentApproval: (req) =>
    set((s) => ({
      agentApprovals: {
        ...s.agentApprovals,
        [approvalKey(req.study_id, req.role, req.iteration)]: req,
      },
    })),
  resolveAgentApproval: (studyId, role, iter) =>
    set((s) => {
      const key = approvalKey(studyId, role, iter)
      if (!(key in s.agentApprovals)) return s
      const next = { ...s.agentApprovals }
      delete next[key]
      return { agentApprovals: next }
    }),
}))

// Convenience helpers (kept here so components don't re-import api).
export type {
  MetricTarget,
  StudySummary,
  StudyStatusResponse,
} from '../api/client'