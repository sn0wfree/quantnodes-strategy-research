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

/** Live event for the event timeline widget */
export interface LiveEvent {
  type: 'phase' | 'agent' | 'knowledge' | 'review' | 'retry' | 'evidence' | 'directive' | 'other'
  message: string
  timestamp: number
  round?: number
  /** Monotonic sequence — survives the 50-entry buffer truncation and
   * same-millisecond collisions (used as the consume cursor). */
  seq?: number
  /** Original SSE payload for agent_* events, preserved so StudyChat
   * can render structured agent cards (tool calls / output etc.). */
  raw?: { type: string; data: Record<string, unknown> }
}

/** Pending HITL (novelty gate) interrupt — carries the REAL DB
 * interrupt_id so the approval card can answer
 * POST /study/{id}/interrupts/{iid}/respond (synthetic client-side ids
 * used to 404). Populated from study_paused SSE events. */
export interface HitlInterrupt {
  study_id: string
  interrupt_id: string
  round?: number
  hypothesis: string
  message: string
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
  /** Pending HITL novelty-gate interrupt (single slot — the gate pauses
   * the whole round, so at most one can be open per study). */
  hitlInterrupt: HitlInterrupt | null

  // ── Live activity tracking (Phase D) ──────────────────────────
  /** Current phase within a round (e.g. "researcher", "execution", "evaluation") */
  currentPhase: string | null
  /** Current agent running within the phase */
  currentAgent: string | null
  /** Timestamp when currentPhase started (Date.now()) */
  phaseStartedAt: number | null
  /** Per-node DAG status from SSE events */
  nodeStatuses: Record<string, string>
  /** Recent SSE events for the event timeline (max 50) */
  recentEvents: LiveEvent[]

  setCurrent: (s: StudyStatusResponse | null) => void
  setList: (rows: StudySummary[]) => void
  setBusy: (b: boolean) => void
  setError: (e: string) => void
  reset: () => void
  enqueueAgentApproval: (req: AgentApprovalRequest) => void
  resolveAgentApproval: (studyId: string, role: string | null, iter: number | undefined) => void
  setHitlInterrupt: (req: HitlInterrupt) => void
  clearHitlInterrupt: (studyId?: string) => void

  // ── Live activity actions ─────────────────────────────────────
  setPhase: (phase: string | null) => void
  setAgent: (agent: string | null) => void
  updateNodeStatus: (nodeId: string, status: string) => void
  addLiveEvent: (event: Omit<LiveEvent, 'timestamp'>) => void
  clearLiveActivity: () => void
}

/** Monotonic counter for live events — the StudyChat consume cursor
 * must keep working once the buffer starts evicting old entries. */
let liveEventSeq = 0

const approvalKey = (studyId: string, role: string | null, iter: number | undefined) =>
  `${studyId}::${role ?? ''}::${iter ?? 0}`

export const useStudyStore = create<StudyState>()((set) => ({
  current: null,
  list: [],
  busy: false,
  error: '',
  agentApprovals: {},
  hitlInterrupt: null,
  // Live activity defaults
  currentPhase: null,
  currentAgent: null,
  phaseStartedAt: null,
  nodeStatuses: {},
  recentEvents: [],

  setCurrent: (current) => set({ current }),
  setList: (list) => set({ list }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  reset: () => set({
    current: null, list: [], busy: false, error: '', agentApprovals: {},
    hitlInterrupt: null,
    currentPhase: null, currentAgent: null, phaseStartedAt: null,
    nodeStatuses: {}, recentEvents: [],
  }),
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
  setHitlInterrupt: (req) => set({ hitlInterrupt: req }),
  clearHitlInterrupt: (studyId) =>
    set((s) => {
      if (!s.hitlInterrupt) return s
      // Scoped clear: only drop the slot when it belongs to this study
      // (no studyId = unconditional clear on reset).
      if (studyId && s.hitlInterrupt.study_id !== studyId) return s
      return { hitlInterrupt: null }
    }),

  // ── Live activity actions ─────────────────────────────────────
  setPhase: (phase) => set({
    currentPhase: phase,
    phaseStartedAt: phase ? Date.now() : null,
  }),
  setAgent: (agent) => set({ currentAgent: agent }),
  updateNodeStatus: (nodeId, status) =>
    set((s) => ({
      nodeStatuses: { ...s.nodeStatuses, [nodeId]: status },
    })),
  addLiveEvent: (event) =>
    set((s) => ({
      recentEvents: [
        { ...event, timestamp: Date.now(), seq: ++liveEventSeq },
        ...s.recentEvents,
      ].slice(0, 50),
    })),
  clearLiveActivity: () => set({
    currentPhase: null,
    currentAgent: null,
    phaseStartedAt: null,
    nodeStatuses: {},
    recentEvents: [],
  }),
}))

// Convenience helpers (kept here so components don't re-import api).
export type {
  MetricTarget,
  StudySummary,
  StudyStatusResponse,
} from '../api/client'