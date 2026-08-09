import type { SSEHandler, SSEEventType } from './types'
import {
  textStarted,
  textDelta,
  textEnded,
  thinkingStart,
  thinkingDelta,
  thinkingDone,
  thinkingEnd,
} from './textHandlers'
import { toolCall, toolResult, toolProgress, assistantMessage, fileEdit, table, chart, image } from './messageHandlers'
import { messageReceived, attemptStarted, queuePaused, queueState } from './queueHandlers'
import {
  sessionTotalTokens,
  llmUsage,
  compact,
  agentDone,
  errorEvent,
} from './controlHandlers'
import { agentStatus, agentLoop, dagUpdate, progress } from './agentHandlers'
import {
  goalUpdated,
  goalEvidenceAdded,
  goalCompleted,
  sessionMetaUpdated,
} from './metaHandlers'
import {
  studyRound,
  studyCompleted,
  studyFailed,
  studyBudgetLimited,
  studyPaused,
  studyResumed,
  studyInterrupted,
  studyCancelled,
  studyStarted,
  studyQueued,
  studyMonitoringStarted,
  studyDriftDetected,
  studyMonitorCheck,
} from './studyHandlers'
import {
  subagentStarted,
  subagentToolCall,
  subagentToolResult,
  subagentTextDelta,
  subagentCompleted,
  subagentFailed,
} from './subagentHandlers'
import { todoUpdated } from './todoHandlers'
import { permissionRequest, permissionResult } from './permissionHandlers'

/**
 * Dispatch table mapping every registered SSE event type to its handler.
 *
 * Event types absent from this table (file_edit / table / chart / image)
 * are registered in EventSource but intentionally have no handler — see
 * the TODO in types.ts EVENT_TYPES comment. The dispatcher in useSSE
 * drops them silently, preserving the original inline-switch behavior.
 */
export const HANDLERS: Partial<Record<SSEEventType, SSEHandler>> = {
  'text.started': textStarted,
  text_delta: textDelta,
  'text.ended': textEnded,
  thinking_start: thinkingStart,
  thinking_delta: thinkingDelta,
  thinking_done: thinkingDone,
  thinking_end: thinkingEnd,
  tool_call: toolCall,
  tool_result: toolResult,
  tool_progress: toolProgress,
  assistant_message: assistantMessage,
  // Block-part handlers (P6) — register so the dispatcher doesn't
  // drop them silently. Defense-in-depth: the backend does not
  // currently emit these at SSE time (see projector.py:985-994);
  // when emission lands, the assistant message will pick up the
  // part automatically.
  file_edit: fileEdit,
  table,
  chart,
  image,
  message_received: messageReceived,
  'attempt.started': attemptStarted,
  queue_paused: queuePaused,
  queue_state: queueState,
  session_total_tokens: sessionTotalTokens,
  llm_usage: llmUsage,
  compact,
  agent_done: agentDone,
  error: errorEvent,
  agent_status: agentStatus,
  agent_loop: agentLoop,
  dag_update: dagUpdate,
  progress,
  goal_updated: goalUpdated,
  goal_evidence_added: goalEvidenceAdded,
  goal_completed: goalCompleted,
  session_meta_updated: sessionMetaUpdated,
  // Study task system (Phase 5 — StudyScheduler emits study_* via EventStore)
  study_queued: studyQueued,
  study_started: studyStarted,
  study_round: studyRound,
  study_completed: studyCompleted,
  study_failed: studyFailed,
  study_budget_limited: studyBudgetLimited,
  study_paused: studyPaused,
  study_resumed: studyResumed,
  study_interrupted: studyInterrupted,
  study_cancelled: studyCancelled,
  study_monitoring_started: studyMonitoringStarted,
  study_monitor_check: studyMonitorCheck,
  study_drift_detected: studyDriftDetected,
  // Subagent lifecycle
  subagent_started: subagentStarted,
  subagent_tool_call: subagentToolCall,
  subagent_tool_result: subagentToolResult,
  subagent_text_delta: subagentTextDelta,
  subagent_completed: subagentCompleted,
  subagent_failed: subagentFailed,
  // Todo / task tracking
  todo_updated: todoUpdated,
  // Tier 1 A1: permission gate handshake (backend -> frontend).
  permission_request: permissionRequest,
  permission_result: permissionResult,
}