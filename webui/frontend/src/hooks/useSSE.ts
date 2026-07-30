import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useGoalStore } from '../stores/goal'
import { useToastStore } from '../stores/toast'
import { useSSEStore } from '../stores/sse'
import { useSessionStore } from '../stores/session'

type SSEEventType =
  | 'text_delta' | 'tool_call' | 'tool_result' | 'tool_progress'
  | 'thinking_delta' | 'thinking_done' | 'thinking_start' | 'thinking_end'
  | 'file_edit' | 'table' | 'chart' | 'image'
  | 'agent_status' | 'agent_loop' | 'agent_done' | 'assistant_message'
  | 'dag_update' | 'progress' | 'message_received' | 'error'
  | 'session_meta_updated'
  | 'goal_updated' | 'goal_evidence_added' | 'goal_completed'
  | 'attempt.started' | 'queue_paused' | 'queue_state'
  | 'llm_usage' | 'session_total_tokens'

export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectCount = useRef(0)

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const setStreamingText = useChatStore((s) => s.setStreamingText)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
  const setQueuePaused = useChatStore((s) => s.setQueuePaused)
  const setQueueLength = useChatStore((s) => s.setQueueLength)
  const setTokensUsed = useChatStore((s) => s.setTokensUsed)
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

      const messageId = data.message_id as string | undefined

      switch (event) {
        case 'text_delta': {
          // Backend sends {"text": "delta_content", "message_id": "..."}
          const text = (data.text || data.delta) as string
          if (text && messageId) {
            // Append to the streaming message's text part
            updateMessage(messageId, (msg) => {
              const textPart = msg.parts.find((p) => p.type === 'text')
              if (textPart && textPart.type === 'text') {
                textPart.text += text
              }
            })
          }
          // Also update the global streaming text for the StreamingText component
          appendStreamingText(text || '')
          break
        }
        case 'assistant_message': {
          // Backend sends {"content": "full text", "message_id": "..."}
          const content = data.content as string
          if (content && messageId) {
            updateMessage(messageId, (msg) => {
              const textPart = msg.parts.find((p) => p.type === 'text')
              if (textPart && textPart.type === 'text') {
                // Only replace if new content is longer (prevents max_iter
                // from wiping accumulated text_delta content)
                if (content.length > textPart.text.length || textPart.text === '') {
                  textPart.text = content
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
                parts: [{ type: 'text', text: userContent }],
                created_at: createdAt,
              })
            }
          }

          // Create assistant placeholder with backend's assistant_message_id.
          // Uses the SAME created_at as the user message so they sort
          // together within the same exchange. Queue metadata is attached
          // so AssistantMessage can render the "等待中... 2/3" state.
          if (assistantMsgId) {
            addMessage({
              id: assistantMsgId,
              session_id: sessionId!,
              role: 'assistant',
              parts: [{ type: 'text', text: '' }],
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
          const { total_tokens } = data as { total_tokens: number }
          if (sessionId && typeof total_tokens === 'number') {
            setTokensUsed(sessionId, total_tokens)
          }
          break
        }
        case 'llm_usage': {
          // Per-call usage delta. Backend accumulates into
          // session_total_tokens and re-emits; we don't need to add
          // here, but use it as a fallback in case session_total_tokens
          // is dropped.
          const d = data as {
            input_tokens?: number
            output_tokens?: number
            prompt_tokens?: number
            completion_tokens?: number
            total_tokens?: number
          }
          const inc =
            d.total_tokens ??
            (d.input_tokens ?? d.prompt_tokens ?? 0) +
              (d.output_tokens ?? d.completion_tokens ?? 0)
          if (sessionId && inc > 0) {
            const current =
              useChatStore.getState().tokensUsed.get(sessionId) ?? 0
            setTokensUsed(sessionId, current + inc)
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
      addMessage,
      updateMessage,
      setStreamingMessage,
      setStreamingText,
      appendStreamingText,
      setTokensUsed,
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
      reconnectCount.current = 0
      useSSEStore.getState().setStatus('connected')
    }

    es.onerror = () => {
      es.close()
      useSSEStore.getState().setStatus('disconnected')
      const delay = Math.min(1000 * Math.pow(2, reconnectCount.current), 30000)
      reconnectCount.current++
      reconnectTimer.current = setTimeout(connect, delay)
    }

    const eventTypes = [
      'text_delta', 'tool_call', 'tool_result', 'tool_progress',
      'thinking_start', 'thinking_delta', 'thinking_done', 'thinking_end',
      'file_edit', 'table', 'chart', 'image',
      'agent_status', 'agent_loop', 'agent_done', 'assistant_message',
      'dag_update', 'progress', 'message_received', 'error',
      'session_meta_updated',
      'goal_updated', 'goal_evidence_added', 'goal_completed',
      'compact',
      'llm_usage', 'session_total_tokens',
    ]
    eventTypes.forEach((type) => es.addEventListener(type, handleEvent))

    sourceRef.current = es
  }, [sessionId, handleEvent])

  useEffect(() => {
    connect()
    return () => {
      sourceRef.current?.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connect])
}
