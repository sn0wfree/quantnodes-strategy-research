import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useToastStore } from '../stores/toast'
import { useSSEStore } from '../stores/sse'
import { useSessionStore } from '../stores/session'

type SSEEventType =
  | 'text_delta' | 'tool_call' | 'tool_result' | 'thinking_delta'
  | 'thinking_done' | 'thinking_start' | 'thinking_end'
  | 'file_edit' | 'table' | 'chart' | 'image'
  | 'agent_status' | 'agent_loop' | 'agent_done' | 'assistant_message'
  | 'dag_update' | 'progress' | 'message_received' | 'error'
  | 'session_meta_updated'

export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectCount = useRef(0)

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const setStreamingText = useChatStore((s) => s.setStreamingText)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
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
                textPart.text = content
              }
            })
          }
          break
        }
        case 'tool_call': {
          const { message_id: mid, id, name, arguments: args } = data as {
            message_id: string
            id: string
            name: string
            arguments: string
          }
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
          const { message_id: mid, id, result, status } = data as {
            message_id: string
            id: string
            result: string
            status: string
          }
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
          // Also handles the case where Composer.tsx's optimistic user
          // message is still using its temp id (SSE may arrive before the
          // /send_async response does).
          const {
            user_message_id: userMsgId,
            assistant_message_id: assistantMsgId,
            attempt_id: attemptId,
            content: userContent,
          } = data as {
            user_message_id?: string
            assistant_message_id?: string
            attempt_id?: string
            content?: string
            message_id?: string
          }
          // Resolve user message id (may be in user_message_id or legacy message_id)
          const userId = userMsgId || (data.message_id as string | undefined)

          // Find and rename any temp user message (Composer optimistic add).
          // We use getState (not setState callback) so we sidestep immer's
          // type inference for nested Maps.
          if (userId) {
            const state = useChatStore.getState()
            const next = new Map(state.messages)
            let renamed = false
            next.forEach((m, id) => {
              if (
                m.role === 'user' &&
                userContent !== undefined &&
                m.parts[0]?.type === 'text' &&
                (m.parts[0] as { text: string }).text === userContent &&
                id !== userId
              ) {
                next.delete(id)
                if (!next.has(userId)) {
                  next.set(userId, { ...m, id: userId })
                }
                renamed = true
              }
            })
            // If user message doesn't exist yet (SSE arrived before
            // Composer optimistic), create it now.
            if (!next.has(userId) && userContent !== undefined) {
              next.set(userId, {
                id: userId,
                session_id: sessionId!,
                role: 'user',
                parts: [{ type: 'text', text: userContent }],
                created_at: Date.now() / 1000,
              })
              renamed = true
            }
            if (renamed) {
              useChatStore.setState({ messages: next })
            }
          }

          // Create assistant placeholder with backend's assistant_message_id
          if (assistantMsgId) {
            addMessage({
              id: assistantMsgId,
              session_id: sessionId!,
              role: 'assistant',
              parts: [{ type: 'text', text: '' }],
              created_at: Date.now() / 1000,
            })
            setStreamingMessage(assistantMsgId)
            setStreamingText('')
            // Touch attemptId to avoid unused-var warning (referenced for logging later)
            void attemptId
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
        case 'agent_done': {
          // AgentLoop finished — clear streaming state
          setStreamingMessage(null)
          break
        }
        case 'error': {
          const error = data.error as string
          if (error) {
            addToast('error', error)
          }
          setStreamingMessage(null)
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
      'text_delta', 'tool_call', 'tool_result',
      'thinking_start', 'thinking_delta', 'thinking_done', 'thinking_end',
      'file_edit', 'table', 'chart', 'image',
      'agent_status', 'agent_loop', 'agent_done', 'assistant_message',
      'dag_update', 'progress', 'message_received', 'error',
      'session_meta_updated',
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
