import type { Agent } from '../../stores/agents'
import type { Message } from '../../stores/chat'
import type { Goal } from '../../stores/goal'

/**
 * DAG/workflow node execution status. Matches `DAGNodeData.status`
 * in `components/workflow/DAGNode.tsx`.
 */
export type NodeStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'

export type SSEEventType =
  | 'text.started'
  | 'text_delta'
  | 'text.ended'
  | 'tool_call'
  | 'tool_result'
  | 'tool_progress'
  | 'thinking_delta'
  | 'thinking_done'
  | 'thinking_start'
  | 'thinking_end'
  | 'file_edit'
  | 'table'
  | 'chart'
  | 'image'
  | 'agent_status'
  | 'agent_loop'
  | 'agent_done'
  | 'assistant_message'
  | 'dag_update'
  | 'progress'
  | 'message_received'
  | 'error'
  | 'session_meta_updated'
  | 'goal_updated'
  | 'goal_evidence_added'
  | 'goal_completed'
  | 'attempt.started'
  | 'queue_paused'
  | 'queue_state'
  | 'llm_usage'
  | 'session_total_tokens'
  | 'compact'
  | 'study_queued'
  | 'study_started'
  | 'study_round'
  | 'study_completed'
  | 'study_failed'
  | 'study_budget_limited'
  | 'study_paused'
  | 'study_resumed'
  | 'study_interrupted'
  | 'study_cancelled'
  | 'study_monitoring_started'
  | 'study_monitor_check'
  | 'study_drift_detected'
  | 'subagent_started'
  | 'subagent_tool_call'
  | 'subagent_tool_result'
  | 'subagent_text_delta'
  | 'subagent_completed'
  | 'subagent_failed'
  | 'todo_updated'

export const EVENT_TYPES: SSEEventType[] = [
  'text.started', 'text_delta', 'text.ended',
  'tool_call', 'tool_result', 'tool_progress',
  'thinking_start', 'thinking_delta', 'thinking_done', 'thinking_end',
  // TODO(feature): file_edit/table/chart/image listeners are
  // registered but have NO switch cases (silently dropped) and the
  // backend never emits them (only the unused FILE_EDIT enum in
  // api/session/event_v2.py). The blocks (FileEditBlock etc.) are
  // reachable only via DB-loaded parts the backend never produces.
  // Wire these once the block-part emission lands in service.py.
  'file_edit', 'table', 'chart', 'image',
  'agent_status', 'agent_loop', 'agent_done', 'assistant_message',
  'dag_update', 'progress', 'message_received', 'error',
  'session_meta_updated',
  'goal_updated', 'goal_evidence_added', 'goal_completed',
  'compact',
  'llm_usage', 'session_total_tokens',
  'attempt.started', 'queue_paused', 'queue_state',
  // Study task system (StudyScheduler → EventStore → SSE)
  'study_queued', 'study_started', 'study_round',
  'study_completed', 'study_failed', 'study_budget_limited',
  'study_paused', 'study_resumed', 'study_interrupted', 'study_cancelled',
  'study_monitoring_started', 'study_monitor_check',
  'study_drift_detected',
  // Subagent events
  'subagent_started', 'subagent_tool_call', 'subagent_tool_result',
  'subagent_text_delta', 'subagent_completed', 'subagent_failed',
  // Todo / task tracking
  'todo_updated',
]

/**
 * SSE handler context — the bundled set of store mutators + session
 * scope the event handlers need.
 *
 * Keeping this in one object (instead of passing ~13 args) makes the
 * handler signature stable and the deps array in useSSE trivially
 * correct (one object identity).
 *
 * The `state.*` accessors are read-through helpers for the lazy-direct
 * lookups the original inline switch performed (e.g. reading the current
 * `messages` Map to check for an existing error bubble). Isolating them
 * here keeps the handler modules free of cross-store imports.
 */
export interface SSEContext {
  sessionId: string | null
  // Chat store mutators
  addMessage: (m: Message) => void
  updateMessage: (id: string, updater: (m: Message) => void) => void
  setStreamingMessage: (id: string | null) => void
  setStreamingText: (s: string) => void
  appendStreamingText: (s: string) => void
  setQueuePaused: (sessionId: string, paused: boolean) => void
  setQueueLength: (sessionId: string, length: number) => void
  setTokensUsed: (sessionId: string, tokens: number) => void
  markTotalTokensSeen: (sessionId: string) => void
  setActiveAttempt: (id: string | null) => void
  setLastCompaction: (c: { layer: string; timestamp: number } | null) => void
  // Per-part streaming preview buffer (opencode-style). text_delta
  // and thinking_delta write into this so the first character of a
  // part is visible the instant it arrives. Cleared on terminal
  // events (text.ended / thinking_done / tool_result) by the same
  // handler that closes the part.
  accumulatePartText: (partId: string, delta: string) => void
  clearPartAccum: (partId: string) => void
  // Read-through state helpers (snapshot reads of the chat store)
  state: {
    getMessage: (id: string) => Message | undefined
    getMessages: () => IterableIterator<Message> | Message[]
    isQueuePaused: (sessionId: string) => boolean
    hasSeenTotalTokens: (sessionId: string) => boolean
    getTokensUsed: (sessionId: string) => number
  }
  // Agent store
  updateAgent: (id: string, updater: (a: Agent) => void) => void
  // Workflow store
  updateNodeStatus: (id: string, status: NodeStatus) => void
  setExecutionProgress: (p: number) => void
  // Goal store
  setGoal: (g: Goal | null) => void
  updateGoal: (updater: (g: Goal) => void) => void
  // Toast store
  addToast: (kind: 'error' | 'info' | 'success', msg: string) => void
  // Session store meta update (auth-side session list patching)
  patchSessionMeta: (session_id: string, patch: Record<string, unknown>) => void
}

export type SSEHandler = (data: Record<string, unknown>, ctx: SSEContext) => void