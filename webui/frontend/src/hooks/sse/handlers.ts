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
import { toolCall, toolResult, toolProgress, assistantMessage } from './messageHandlers'
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
}