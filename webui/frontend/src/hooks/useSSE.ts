import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useGoalStore } from '../stores/goal'
import { useToastStore } from '../stores/toast'
import { useSSEStore } from '../stores/sse'
import { useSessionStore } from '../stores/session'
import { EVENT_TYPES, HANDLERS, type SSEContext, type SSEEventType } from './sse'

/**
 * Subscribe to the session's SSE event stream (/api/chat/events).
 *
 * Contract with the backend (api/session/service.py + routers/chat.py):
 * - One EventSource per session; the browser reconnects natively and
 *   resends `Last-Event-ID`, and the backend replays buffered events
 *   from that id (sse_buffer).
 * - Events carry `{ event_type, message_id, session_id, ... }` where
 *   `message_id` is the ATTEMPT's assistant message id (added by
 *   service.event_callback to every event's data).
 * - The streaming state machine is: `message_received` (user message
 *   echo + queued assistant placeholder, `queue_status: queued` if
 *   behind other attempts) → `attempt.started` (queue consumer picks
 *   up the attempt; frontend switches `streamingMessageId`) →
 *   `text.started` / `text_delta` / `text.ended` (+ tool/thinking
 *   events) → `assistant_message` (final content) → `agent_done`
 *   (streaming cleared). `attempt.completed` carries token usage.
 * - Queue control: `queue_paused` (after an explicit cancel; UI shows
 *   QueuePauseBanner until `resume_queue`), `queue_state` snapshots.
 * - Token accounting: `session_total_tokens` is authoritative (sets
 *   the cumulative); `llm_usage` deltas are only a fallback.
 *
 * State that must survive a reload (agents, DAG, goal panels) is
 * intentionally NOT rebuilt here — see the TODO on the goal handlers.
 *
 * Implementation: each event type is dispatched to a handler in
 * hooks/sse/handlers.ts (split from the original 650-line inline
 * switch). The handlers receive a stable `SSEContext` object bundling
 * the store mutators + read-through state helpers — this keeps the
 * deps array trivial (single context identity) while preserving the
 * exact behavior of the inline switch.
 */
export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const isFirstConnect = useRef(true)
  const prevSessionId = useRef<string | null>(null)
  // Consecutive rejected connections (e.g. 403 — session not owned by
  // this user) cap the reconnect loop; the browser reports CLOSED for
  // non-2xx responses and we would otherwise retry forever, hammering
  // the backend with 403s every second.
  const failedAttempts = useRef(0)
  const MAX_FAILED_ATTEMPTS = 3

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const setStreamingText = useChatStore((s) => s.setStreamingText)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
  const accumulatePartText = useChatStore((s) => s.accumulatePartText)
  const clearPartAccum = useChatStore((s) => s.clearPartAccum)
  const setQueuePaused = useChatStore((s) => s.setQueuePaused)
  const setQueueLength = useChatStore((s) => s.setQueueLength)
  const setTokensUsed = useChatStore((s) => s.setTokensUsed)
  const markTotalTokensSeen = useChatStore((s) => s.markTotalTokensSeen)
  const updateAgent = useAgentStore((s) => s.updateAgent)
  const updateNodeStatus = useWorkflowStore((s) => s.updateNodeStatus)
  const addToast = useToastStore((s) => s.addToast)

  const handleEvent = useCallback(
    (e: MessageEvent) => {
      const event = e.type as SSEEventType
      let data: Record<string, unknown>
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }

      // Pull read-through state snapshots at dispatch time so handlers
      // don't need direct store imports (keeps them unit-testable and
      // free of cross-store init-order concerns). Each helper reads the
      // current store state lazily — exactly matching the original
      // inline `useChatStore.getState().X.get(Y)` lookups.
      const ctx: SSEContext = {
        sessionId,
        addMessage,
        updateMessage,
        setStreamingMessage,
        setStreamingText,
        appendStreamingText,
        setQueuePaused,
        setQueueLength,
        setTokensUsed,
        markTotalTokensSeen,
        setActiveAttempt: (id) => useChatStore.getState().setActiveAttempt(id),
        setLastCompaction: (c) => useChatStore.getState().setLastCompaction(c),
        accumulatePartText,
        clearPartAccum,
        state: {
          getMessage: (id) => useChatStore.getState().messages.get(id),
          getMessages: () => useChatStore.getState().messages.values(),
          isQueuePaused: (sid) => !!useChatStore.getState().queuePaused.get(sid),
          hasSeenTotalTokens: (sid) => !!useChatStore.getState().totalTokensSeen.get(sid),
          getTokensUsed: (sid) => useChatStore.getState().tokensUsed.get(sid) ?? 0,
        },
        updateAgent,
        updateNodeStatus,
        setExecutionProgress: (p) => useWorkflowStore.getState().setExecutionProgress(p),
        setGoal: (g) => useGoalStore.getState().setGoal(g),
        updateGoal: (u) => useGoalStore.getState().updateGoal(u),
        addToast,
        patchSessionMeta: (sid, patch) =>
          useSessionStore.setState((state) => ({
            sessions: state.sessions.map((sess) =>
              sess.id === sid ? { ...sess, ...patch } : sess,
            ),
          })),
      }

      const handler = HANDLERS[event]
      if (handler) handler(data, ctx)
    },
    [
      sessionId,
      addMessage,
      updateMessage,
      setStreamingMessage,
      setStreamingText,
      appendStreamingText,
      accumulatePartText,
      clearPartAccum,
      setQueuePaused,
      setQueueLength,
      setTokensUsed,
      markTotalTokensSeen,
      updateAgent,
      updateNodeStatus,
      addToast,
    ],
  )

  const connect = useCallback(() => {
    if (!sessionId) return
    if (sourceRef.current) {
      sourceRef.current.close()
    }
    // Reset per-session state when the session changes
    if (prevSessionId.current !== sessionId) {
      isFirstConnect.current = true
      failedAttempts.current = 0
      prevSessionId.current = sessionId
    }

    useSSEStore.getState().setStatus('connecting')

    const token = localStorage.getItem('sr-auth')
    let parsedToken = ''
    try {
      parsedToken = token ? JSON.parse(token).state.token : ''
    } catch {}

    const params = new URLSearchParams({ session_id: sessionId })
    if (parsedToken) params.set('token', parsedToken)
    const es = new EventSource(`/api/chat/events?${params}`)

    es.onopen = () => {
      failedAttempts.current = 0
      useSSEStore.getState().setStatus('connected')
      if (isFirstConnect.current) {
        isFirstConnect.current = false
      } else {
        // Reconnect: clean stale streaming state that may be stuck
        const chat = useChatStore.getState()
        if (chat.streamingMessageId) {
          chat.setStreamingMessage(null)
          chat.setActiveAttempt(null)
        }
        console.debug('[SSE] reconnected — cleared stale streaming state')
      }
    }

    es.onerror = (e) => {
      // Let the browser's native EventSource reconnect automatically —
      // it sends the Last-Event-ID header so missed events are replayed.
      const target = e.currentTarget as EventSource | null
      if (target) {
        console.debug('[SSE] onerror readyState=%s', target.readyState)
        if (target.readyState === EventSource.CLOSED) {
          // Permanent disconnect — browser won't auto-reconnect.
          // Create a new EventSource after a short delay, but give up
          // after MAX_FAILED_ATTEMPTS consecutive rejections (server
          // 403/404 — e.g. a session owned by a different user).
          failedAttempts.current += 1
          if (failedAttempts.current >= MAX_FAILED_ATTEMPTS) {
            console.warn(
              `[SSE] connection rejected ${failedAttempts.current} times for session ${sessionId}, giving up`,
            )
            useSSEStore.getState().setStatus('disconnected')
            sourceRef.current?.close()
            return
          }
          console.warn('[SSE] EventSource CLOSED, reconnecting in 1s...')
          setTimeout(() => connect(), 1000)
          return
        }
      } else {
        console.debug('[SSE] onerror')
      }
      useSSEStore.getState().setStatus('disconnected')
    }

    EVENT_TYPES.forEach((type) => es.addEventListener(type, handleEvent))

    // Heartbeat handling: the backend sends periodic SSE comment lines
    // (no event type, just ": heartbeat\n\n"). We also listen for the
    // explicit "heartbeat" event in case the backend uses named events
    // in the future. Receiving either signal keeps the connection
    // marked as alive — defending against the browser prematurely
    // reporting onerror on idle streams.
    es.addEventListener('heartbeat', () => {
      useSSEStore.getState().setStatus('connected')
    })

    sourceRef.current = es
  }, [sessionId, handleEvent])

  useEffect(() => {
    connect()
    return () => {
      sourceRef.current?.close()
    }
  }, [connect])
}