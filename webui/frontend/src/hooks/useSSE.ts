import { useEffect, useRef, useCallback } from 'react'
import { useChatStore, type TextPart } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useGoalStore } from '../stores/goal'
import { useToastStore } from '../stores/toast'
import { useSSEStore } from '../stores/sse'
import { useSessionStore } from '../stores/session'

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
 */
export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const setStreamingText = useChatStore((s) => s.setStreamingText)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
  const setQueuePaused = useChatStore((s) => s.setQueuePaused)
  const setQueueLength = useChatStore((s) => s.setQueueLength)
  const setTokensUsed = useChatStore((s) => s.setTokensUsed)
  const markTotalTokensSeen = useChatStore((s) => s.markTotalTokensSeen)
  const updateAgent = useAgentStore((s) => s.updateAgent)
  const updateNodeStatus = useWorkflowStore((s) => s.updateNodeStatus)
  const addToast = useToastStore((s) => s.addToast)

  type SSEEventType =
    | 'text.started' | 'text_delta' | 'text.ended'
    | 'tool_call' | 'tool_result' | 'tool_progress'
    | 'thinking_delta' | 'thinking_done' | 'thinking_start' | 'thinking_end'
    | 'file_edit' | 'table' | 'chart' | 'image'
    | 'agent_status' | 'agent_loop' | 'agent_done' | 'assistant_message'
    | 'dag_update' | 'progress' | 'message_received' | 'error'
    | 'session_meta_updated'
    | 'goal_updated' | 'goal_evidence_added' | 'goal_completed'
    | 'attempt.started' | 'queue_paused' | 'queue_state'
    | 'llm_usage' | 'session_total_tokens' | 'compact'

  const handleEvent = useCallback(
    (e: MessageEvent) => {
      const event = e.type as SSEEventType
      let data: Record<string, unknown>
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }

      const messageId = data.message_id as string | undefined

      switch (event) {
        case 'text.started': {
          // Backend (opencode-style protocol) signals a new text segment.
          // Push a fresh text part with this id so subsequent text_delta
          // events with the same text_id can route to it.
          const textId = data.text_id as string | undefined
          if (textId && messageId) {
            updateMessage(messageId, (msg) => {
              const existing = msg.parts.find(
                (p) => p.type === 'text' && (p as TextPart).id === textId
              )
              if (!existing) {
                msg.parts.push({ type: 'text', id: textId, text: '' })
              }
            })
          }
          break
        }
        case 'text_delta': {
          // 3-step text protocol: text_delta MUST carry text_id (hard-break
          // — no legacy "always first text part" fallback). We findLast by
          // id so deltas route to the correct text segment even when text
          // and tool calls interleave across LLM iterations.
          const text = (data.text || data.delta) as string
          const textId = data.text_id as string | undefined
          if (text && messageId) {
            if (!textId) {
              // Protocol error: drop the chunk. This protects against
              // future regressions where backend forgets to emit
              // text.started first.
              console.warn('[useSSE] text_delta without text_id, dropping chunk')
            } else {
              updateMessage(messageId, (msg) => {
                for (let i = msg.parts.length - 1; i >= 0; i--) {
                  const p = msg.parts[i]
                  if (p && p.type === 'text' && (p as TextPart).id === textId) {
                    (p as TextPart).text += text
                    return
                  }
                }
                // Orphan: text.started hasn't arrived yet (replay / late
                // join). Push a new part with this id to keep the chunk.
                msg.parts.push({ type: 'text', id: textId, text })
              })
            }
          }
          // Also update the global streaming text for the StreamingText component
          appendStreamingText(text || '')
          break
        }
        case 'text.ended': {
          // Backend signals the text segment is finalized. Override the
          // text part's content with the authoritative final text (last
          // write wins, same as opencode Text.Ended).
          // Guard: some protocol variants emit text.ended as a pure
          // end-signal without the final text — never wipe the
          // accumulated streaming content with an empty string (B4).
          const textId = data.text_id as string | undefined
          const finalText = (data.text || '') as string
          if (textId && messageId) {
            updateMessage(messageId, (msg) => {
              for (let i = msg.parts.length - 1; i >= 0; i--) {
                const p = msg.parts[i]
                if (p && p.type === 'text' && (p as TextPart).id === textId) {
                  if (finalText) (p as TextPart).text = finalText
                  return
                }
              }
            })
          }
          break
        }
        case 'assistant_message': {
          // Backend sends {"content": "full text", "message_id": "..."}
          // For error messages (message_type='error'), there's no streaming
          // text — the message may not exist yet. Create it as an error
          // bubble with the friendly text + collapsible detail.
          const content = data.content as string
          const messageType = data.message_type as string | undefined
          if (messageId && messageType === 'error' && content) {
            const meta = data.metadata as { details?: string } | undefined
            const details = meta?.details ?? ''
            const existing = useChatStore.getState().messages.get(messageId)
            if (existing) {
              // Update existing placeholder to error type
              updateMessage(messageId, (msg) => {
                msg.message_type = 'error'
                msg.parts = [{ type: 'text', id: `err-${messageId}`, text: content }]
                if (!msg.metadata) msg.metadata = {}
                msg.metadata.status = 'error'
                msg.metadata.details = details
              })
            } else {
              // Create new error bubble
              addMessage({
                id: messageId,
                session_id: sessionId!,
                role: 'assistant',
                parts: [{ type: 'text', id: `err-${messageId}`, text: content }],
                created_at: Date.now() / 1000,
                message_type: 'error',
                metadata: { status: 'error', details },
              })
            }
            break
          }
          // Normal path: find the LAST text part (by id) and replace if
          // the new content is longer. Preserves text_id-routing semantics.
          if (content && messageId) {
            updateMessage(messageId, (msg) => {
              // Find last text part with content (skip empty seeded ones)
              let lastTextIdx = -1
              for (let i = msg.parts.length - 1; i >= 0; i--) {
                const p = msg.parts[i]
                if (p && p.type === 'text') {
                  lastTextIdx = i
                  break
                }
              }
              if (lastTextIdx >= 0) {
                const p = msg.parts[lastTextIdx] as TextPart
                // Only replace if new content is longer (prevents max_iter
                // from wiping accumulated text_delta content)
                if (content.length > p.text.length || p.text === '') {
                  p.text = content
                }
              }
            })
          }
          break
        }
        case 'tool_call': {
          const { message_id: mid, id, name, arguments: rawArgs } = data as {
            message_id: string
            id: string
            name: string
            arguments: string | unknown
          }
          // Always store as JSON string for consistency with DB-loaded messages
          const args =
            typeof rawArgs === 'string'
              ? rawArgs
              : JSON.stringify(rawArgs ?? {})
          updateMessage(mid, (msg) => {
            const existing = msg.parts.find(
              (p) => p.type === 'tool_call' && p.id === id
            )
            if (!existing) {
              msg.parts.push({
                type: 'tool_call',
                id,
                name,
                arguments: args,
                status: 'running',
              })
            }
          })
          break
        }
        case 'tool_result': {
          const { message_id: mid, id, result: rawResult, status } = data as {
            message_id: string
            id: string
            result: string | unknown
            status: string
          }
          // Always store as JSON string for consistency with DB-loaded messages
          const result =
            typeof rawResult === 'string'
              ? rawResult
              : JSON.stringify(rawResult ?? {})
          updateMessage(mid, (msg) => {
            const tc = msg.parts.find(
              (p) => p.type === 'tool_call' && p.id === id
            )
            if (tc && tc.type === 'tool_call') {
              tc.result = result
              tc.status = status as 'done' | 'error'
            }
          })
          break
        }
        case 'tool_progress': {
          const { message_id: mid, id, steps } = data as {
            message_id: string
            id: string
            steps: string[]
          }
          if (mid && id && steps) {
            updateMessage(mid, (msg) => {
              const tc = msg.parts.find(
                (p) => p.type === 'tool_call' && p.id === id
              )
              if (tc && tc.type === 'tool_call') {
                tc.progress = steps
              }
            })
          }
          break
        }
        case 'thinking_start': {
          if (messageId) {
            updateMessage(messageId, (msg) => {
              msg.parts.push({
                type: 'thinking',
                text: '',
                collapsed: true,
              } as any)
            })
          }
          break
        }
        case 'message_received': {
          // Backend signals: user message persisted, attempt created,
          // assistant about to stream. Frontend uses this to create the
          // assistant placeholder with the backend's assistant_message_id
          // so subsequent text_delta / thinking_* / assistant_message events
          // (which carry that same id) can update it correctly.
          //
          // Per-session FIFO queue: backend may attach status="queued" and
          // queue_position/length to indicate the message is waiting behind
          // an in-flight attempt. In that case we create the placeholder
          // but do NOT switch streamingMessageId; we wait for the
          // attempt.started event before kicking off streaming.
          //
          // Uses incremental addMessage (immer set) instead of Map
          // replacement to avoid race conditions with Composer's optimistic
          // addMessage.
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

          // Use backend-authoritative created_at when available
          // (server time.time(), microsecond precision). This ensures
          // user + assistant in the same exchange share the same
          // timestamp, so stable sort groups them correctly.
          const createdAt = backendCreatedAt ?? Date.now() / 1000
          const isQueued = queueStatus === 'queued'

          // Ensure user message exists with correct backend ID.
          if (userId && userContent !== undefined) {
            const existing = useChatStore.getState().messages.get(userId)
            if (existing) {
              updateMessage(userId, (msg) => { msg.created_at = createdAt })
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

          // Create assistant placeholder with backend's assistant_message_id.
          // Uses the SAME created_at as the user message so they sort
          // together within the same exchange. Queue metadata is attached
          // so AssistantMessage can render the "等待中... 2/3" state.
          //
          // The initial text part uses a placeholder id; the real id arrives
          // via text.started once streaming begins.
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
          break
        }
        case 'attempt.started': {
          // Backend queue consumer picked up the next queued attempt and
          // is starting streaming on the assistant_message_id. Frontend
          // switches streamingMessageId to this message and resets the
          // streaming text buffer.
          const mid = (data.message_id as string | undefined) ?? messageId
          if (mid && sessionId) {
            setStreamingMessage(mid)
            setStreamingText('')
            // Clear queue-paused flag once we resume processing
            if (useChatStore.getState().queuePaused.get(sessionId)) {
              setQueuePaused(sessionId, false)
            }
          }
          break
        }
        case 'queue_paused': {
          // Backend queue consumer paused after an explicit cancel; the
          // frontend shows the "队列已暂停" banner with a resume button.
          if (sessionId) {
            setQueuePaused(sessionId, true)
          }
          break
        }
        case 'queue_state': {
          // Backend emitted a snapshot of the current queue length.
          if (sessionId && typeof (data as any).queue_length === 'number') {
            setQueueLength(sessionId, (data as any).queue_length)
          }
          break
        }
        case 'session_total_tokens': {
          // Backend authoritative cumulative for the current attempt.
          // Used by ContextUsageBar to show context window usage.
          // Marks the session so later llm_usage deltas are not added
          // on top (double counting — regression B2).
          const { total_tokens } = data as { total_tokens: number }
          if (sessionId && typeof total_tokens === 'number') {
            setTokensUsed(sessionId, total_tokens)
            markTotalTokensSeen(sessionId)
          }
          break
        }
        case 'llm_usage': {
          // Per-call usage delta. The backend accumulates these into
          // session_total_tokens and re-emits it for every LLM call,
          // so adding here would double-count (regression B2). Only
          // fall back to deltas when the authoritative cumulative
          // event was never seen for this session.
          const d = data as {
            input_tokens?: number
            output_tokens?: number
            prompt_tokens?: number
            completion_tokens?: number
            total_tokens?: number
          }
          if (sessionId && !useChatStore.getState().totalTokensSeen.get(sessionId)) {
            const inc =
              d.total_tokens ??
              (d.input_tokens ?? d.prompt_tokens ?? 0) +
                (d.output_tokens ?? d.completion_tokens ?? 0)
            if (inc > 0) {
              const current =
                useChatStore.getState().tokensUsed.get(sessionId) ?? 0
              setTokensUsed(sessionId, current + inc)
            }
          }
          break
        }
        case 'thinking_delta': {
          const delta = data.delta as string
          if (delta && messageId) {
            updateMessage(messageId, (msg) => {
              const last = msg.parts[msg.parts.length - 1]
              if (last && last.type === 'thinking') {
                last.text += delta
              }
            })
          }
          break
        }
        case 'thinking_done': {
          // Thinking phase finished, first text token arrived
          if (messageId) {
            updateMessage(messageId, (msg) => {
              const last = msg.parts[msg.parts.length - 1]
              if (last && last.type === 'thinking') {
                last.collapsed = true
              }
            })
          }
          break
        }
        case 'thinking_end': {
          // Streaming phase completely finished
          if (messageId) {
            updateMessage(messageId, (msg) => {
              const last = msg.parts[msg.parts.length - 1]
              if (last && last.type === 'thinking') {
                last.collapsed = true
              }
            })
          }
          break
        }
        case 'agent_done': {
          // AgentLoop finished — clear streaming state
          setStreamingMessage(null)
          useChatStore.getState().setActiveAttempt(null)
          break
        }
        case 'compact': {
          const compactData = data as {
            agent_id?: string
            layer?: string
            iteration?: number
            summary?: string
          }
          if (compactData.agent_id) {
            updateAgent(compactData.agent_id, (agent) => {
              agent.compaction_count = (agent.compaction_count || 0) + 1
              agent.last_compaction = {
                layer: compactData.layer || 'unknown',
                timestamp: Date.now(),
              }
            })
          }
          useChatStore.getState().setLastCompaction({
            layer: compactData.layer || 'unknown',
            timestamp: Date.now(),
          })
          break
        }
        case 'error': {
          const error = data.error as string
          if (error) {
            addToast('error', error)
          }
          setStreamingMessage(null)
          useChatStore.getState().setActiveAttempt(null)
          break
        }
        case 'agent_status': {
          // TODO(architecture): agent_*/dag_update handlers only UPDATE
          // entries the store already has; nothing calls addAgent /
          // setDAG with real data (setDAG is only invoked with []), so
          // after a page reload the Agent list and DAG panels are
          // empty until a new run starts. Planned fix: backfill on
          // session load from the run's persisted state.
          const { agent_id, status, ...rest } = data as {
            agent_id: string
            status: string
            [key: string]: unknown
          }
          updateAgent(agent_id, (agent) => {
            agent.status = status as any
            Object.assign(agent, rest)
          })
          break
        }
        case 'agent_loop': {
          const { agent_id, ...loopData } = data as {
            agent_id: string
            [key: string]: unknown
          }
          updateAgent(agent_id, (agent) => {
            Object.assign(agent, loopData)
          })
          break
        }
        case 'dag_update': {
          const { node_id, status } = data as {
            node_id: string
            status: string
          }
          updateNodeStatus(node_id, status as any)
          break
        }
        case 'progress': {
          const { progress } = data as { progress: number }
          useWorkflowStore.getState().setExecutionProgress(progress)
          break
        }
        case 'goal_updated': {
          // TODO(feature): dead chain end-to-end today. No backend
          // emitter produces goal_* events (verified: zero
          // `emit(...goal...)` calls in src/), so GoalTab/CriteriaList/
          // GoalTimeline can only ever render empty states. Planned
          // wiring: the goal service emits these events on
          // start/evidence/complete (docs/goal-workflow-design.md), or
          // the frontend polls /api/goal/status. The handlers below
          // are kept ready for that event contract.
          const goalData = data as any
          if (goalData.goal_id) {
            useGoalStore.getState().setGoal({
              goal_id: goalData.goal_id,
              session_id: goalData.session_id || '',
              status: goalData.status || 'active',
              objective: goalData.objective || '',
              progress_percent: goalData.progress_percent || 0,
              criteria: goalData.criteria || [],
              evidence_count: goalData.evidence_count || 0,
            })
          }
          break
        }
        case 'goal_evidence_added': {
          const evData = data as any
          useGoalStore.getState().updateGoal((g) => {
            g.evidence_count = (g.evidence_count || 0) + 1
            if (evData.progress_percent !== undefined) {
              g.progress_percent = evData.progress_percent
            }
          })
          break
        }
        case 'goal_completed': {
          const compData = data as any
          useGoalStore.getState().updateGoal((g) => {
            g.status = compData.status || 'complete'
            if (compData.recap) g.recap = compData.recap
          })
          break
        }
        case 'session_meta_updated': {
          // Server-side update (e.g. auto-title after first message)
          const { session_id, title, message_count, starred, tags, archived } = data as {
            session_id: string
            title?: string
            message_count?: number
            starred?: boolean
            tags?: string[]
            archived?: boolean
          }
          if (session_id) {
            useSessionStore.setState((state) => ({
              sessions: state.sessions.map((sess) =>
                sess.id === session_id
                  ? {
                      ...sess,
                      ...(title !== undefined ? { title } : {}),
                      ...(message_count !== undefined ? { message_count } : {}),
                      ...(starred !== undefined ? { starred } : {}),
                      ...(tags !== undefined ? { tags } : {}),
                      ...(archived !== undefined ? { archived } : {}),
                    }
                  : sess
              ),
            }))
          }
          break
        }
      }
    },
    [
      sessionId,
      addMessage,
      updateMessage,
      setStreamingMessage,
      setStreamingText,
      appendStreamingText,
      setTokensUsed,
      markTotalTokensSeen,
      updateAgent,
      updateNodeStatus,
      addToast,
    ]
  )

  const connect = useCallback(() => {
    if (!sessionId) return
    if (sourceRef.current) {
      sourceRef.current.close()
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
      useSSEStore.getState().setStatus('connected')
    }

    es.onerror = (e) => {
      // Let the browser's native EventSource reconnect automatically —
      // it sends the Last-Event-ID header so missed events are replayed.
      // We only update status; no manual close/setTimeout reconnect.
      const target = e.currentTarget as EventSource | null
      if (target) {
        console.debug('[SSE] onerror readyState=%s', target.readyState)
      } else {
        console.debug('[SSE] onerror')
      }
      useSSEStore.getState().setStatus('disconnected')
    }

    const eventTypes = [
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
    ]
    eventTypes.forEach((type) => es.addEventListener(type, handleEvent))

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
