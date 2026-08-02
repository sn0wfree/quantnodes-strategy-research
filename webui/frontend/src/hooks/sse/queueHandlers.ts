import type { SSEHandler } from './types'

/**
 * message_received: backend signals "user message persisted, attempt
 * created, assistant about to stream". The frontend uses this to
 * create the assistant placeholder with the backend's
 * `assistant_message_id` so subsequent text_delta / thinking_* /
 * assistant_message events (which carry that same id) can update it
 * correctly.
 *
 * Per-session FIFO queue: backend may attach status="queued" and
 * queue_position/length to indicate the message is waiting behind an
 * in-flight attempt. In that case we create the placeholder but do
 * NOT switch streamingMessageId; we wait for `attempt.started`.
 */
export const messageReceived: SSEHandler = (data, ctx) => {
  const {
    addMessage,
    updateMessage,
    setStreamingMessage,
    setStreamingText,
    setQueueLength,
    sessionId,
    state,
  } = ctx
  const {
    user_message_id: userMsgId,
    assistant_message_id: assistantMsgId,
    content: userContent,
    created_at: backendCreatedAt,
    status: queueStatus,
    queue_position,
    queue_length,
  } = data as {
    user_message_id?: string
    assistant_message_id?: string
    attempt_id?: string
    content?: string
    message_id?: string
    created_at?: number
    status?: 'processing' | 'queued'
    queue_position?: number
    queue_length?: number
  }
  const userId = userMsgId || (data.message_id as string | undefined)

  // Use backend-authoritative created_at when available (server
  // time.time(), microsecond precision). Ensures user + assistant in
  // the same exchange share the same timestamp, so stable sort
  // groups them correctly.
  const createdAt = backendCreatedAt ?? Date.now() / 1000
  const isQueued = queueStatus === 'queued'

  // Ensure user message exists with correct backend ID.
  if (userId && userContent !== undefined) {
    const existing = state.getMessage(userId)
    if (existing) {
      updateMessage(userId, (msg) => {
        msg.created_at = createdAt
      })
    } else {
      addMessage({
        id: userId,
        session_id: sessionId!,
        role: 'user',
        parts: [{ type: 'text', id: `seed-${userId}`, text: userContent }],
        created_at: createdAt,
      })
    }
  }

  // Create assistant placeholder with backend's
  // assistant_message_id. Uses the SAME created_at as the user
  // message so they sort together within the same exchange. Queue
  // metadata is attached so AssistantMessage can render the
  // "等待中... 2/3" state.
  //
  // The initial text part uses a placeholder id; the real id
  // arrives via text.started once streaming begins.
  if (assistantMsgId) {
    addMessage({
      id: assistantMsgId,
      session_id: sessionId!,
      role: 'assistant',
      parts: [{ type: 'text', id: `seed-${assistantMsgId}`, text: '' }],
      created_at: createdAt,
      metadata: {
        queue_position,
        queue_length,
        queue_status: queueStatus ?? 'processing',
      },
    })
    if (!isQueued) {
      // Head-of-queue: kick off streaming immediately (legacy path).
      setStreamingMessage(assistantMsgId)
      setStreamingText('')
    } else if (typeof queue_length === 'number' && sessionId) {
      // Queued: track queue length for banner/UI; do NOT stream.
      setQueueLength(sessionId, queue_length)
    }
  }
}

/**
 * attempt.started: backend queue consumer picked up the next queued
 * attempt and is starting streaming on the assistant_message_id.
 * Frontend switches streamingMessageId to this message and resets
 * the streaming text buffer; clears any queue_paused flag.
 */
export const attemptStarted: SSEHandler = (data, ctx) => {
  const { setStreamingMessage, setStreamingText, setQueuePaused, sessionId, state } = ctx
  // Backend may carry the assistant_message_id either as the top-level
  // `message_id` or re-emit it under the same key (legacy variants).
  // `message_id ?? messageId` in the original code was a no-op; we read
  // `message_id` once and treat absent as "no streaming target yet".
  const mid = data.message_id as string | undefined
  if (!mid || !sessionId) return
  setStreamingMessage(mid)
  setStreamingText('')
  if (state.isQueuePaused(sessionId)) setQueuePaused(sessionId, false)
}

/** queue_paused: backend queue paused after an explicit cancel. */
export const queuePaused: SSEHandler = (_data, ctx) => {
  if (ctx.sessionId) ctx.setQueuePaused(ctx.sessionId, true)
}

/** queue_state: backend snapshot of the current queue length. */
export const queueState: SSEHandler = (data, ctx) => {
  if (!ctx.sessionId) return
  const len = (data as any).queue_length
  if (typeof len === 'number') ctx.setQueueLength(ctx.sessionId, len)
}