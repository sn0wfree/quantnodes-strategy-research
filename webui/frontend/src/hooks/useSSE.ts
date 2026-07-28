import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chat'
import { useAgentStore } from '../stores/agents'
import { useWorkflowStore } from '../stores/workflow'
import { useToastStore } from '../stores/toast'

type SSEEventType =
  | 'text_delta' | 'tool_call' | 'tool_result' | 'thinking_delta'
  | 'thinking_done' | 'thinking_start' | 'thinking_end'
  | 'file_edit' | 'table' | 'chart' | 'image'
  | 'agent_status' | 'agent_loop'
  | 'dag_update' | 'progress'

interface SSEEvent {
  event: string
  data: string
  id?: string
}

export function useSSE(sessionId: string | null) {
  const sourceRef = useRef<EventSource | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const reconnectCount = useRef(0)

  const addMessage = useChatStore((s) => s.addMessage)
  const updateMessage = useChatStore((s) => s.updateMessage)
  const setStreamingMessage = useChatStore((s) => s.setStreamingMessage)
  const appendStreamingText = useChatStore((s) => s.appendStreamingText)
  const updateAgent = useAgentStore((s) => s.updateAgent)
  const addAgent = useAgentStore((s) => s.addAgent)
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

      switch (event) {
        case 'text_delta': {
          const delta = data.delta as string
          appendStreamingText(delta)
          break
        }
        case 'tool_call': {
          const { message_id, id, name, arguments: args } = data as {
            message_id: string
            id: string
            name: string
            arguments: string
          }
          updateMessage(message_id, (msg) => {
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
          const { message_id, id, result, status } = data as {
            message_id: string
            id: string
            result: string
            status: string
          }
          updateMessage(message_id, (msg) => {
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
          const { message_id, thinking_id } = data as {
            message_id: string
            thinking_id: string
          }
          updateMessage(message_id, (msg) => {
            msg.parts.push({
              type: 'thinking',
              text: '',
              collapsed: true,
            } as any)
          })
          break
        }
        case 'thinking_delta': {
          const { message_id, delta } = data as {
            message_id: string
            delta: string
          }
          updateMessage(message_id, (msg) => {
            const last = msg.parts[msg.parts.length - 1]
            if (last && last.type === 'thinking') {
              last.text += delta
            }
          })
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
      }
    },
    [
      addMessage,
      updateMessage,
      setStreamingMessage,
      appendStreamingText,
      updateAgent,
      addAgent,
      updateNodeStatus,
      addToast,
    ]
  )

  const connect = useCallback(() => {
    if (!sessionId) return
    if (sourceRef.current) {
      sourceRef.current.close()
    }

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
    }

    es.onerror = () => {
      es.close()
      const delay = Math.min(1000 * Math.pow(2, reconnectCount.current), 30000)
      reconnectCount.current++
      reconnectTimer.current = setTimeout(connect, delay)
    }

    const eventTypes = [
      'text_delta', 'tool_call', 'tool_result',
      'thinking_start', 'thinking_delta', 'thinking_done', 'thinking_end',
      'file_edit', 'table', 'chart', 'image',
      'agent_status', 'agent_loop',
      'dag_update', 'progress',
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
