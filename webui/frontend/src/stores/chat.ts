import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import { enableMapSet } from 'immer'
import { api } from '../api/client'
import { useSessionStore } from './session'

// Required for immer to handle Map/Set types in chat state
enableMapSet()

export type MessageRole = 'user' | 'assistant' | 'system'

export interface TextPart {
  type: 'text'
  text: string
}

export interface ToolCallPart {
  type: 'tool_call'
  id: string
  name: string
  arguments: string | unknown
  result?: string | unknown
  status: 'pending' | 'running' | 'done' | 'error'
}

export interface ThinkingPart {
  type: 'thinking'
  text: string
  collapsed?: boolean
}

export interface FileEditPart {
  type: 'file_edit'
  file_path: string
  old_content: string
  new_content: string
}

export interface TablePart {
  type: 'table'
  headers: string[]
  rows: string[][]
  caption?: string
}

export interface ChartPart {
  type: 'chart'
  chart_type: 'bar' | 'line' | 'pie' | 'scatter'
  data: unknown[]
  title?: string
}

export interface ImagePart {
  type: 'image'
  url: string
  alt?: string
}

export type MessagePart = TextPart | ToolCallPart | ThinkingPart | FileEditPart | TablePart | ChartPart | ImagePart

export interface Message {
  id: string
  session_id: string
  role: MessageRole
  agent_id?: string
  parts: MessagePart[]
  created_at: number
  metadata?: {
    model?: string
    tokens_used?: number
    iteration?: number
  }
}

interface ChatState {
  messages: Map<string, Message>
  streamingMessageId: string | null
  streamingText: string
  activeAttemptId: string | null
  setMessages: (messages: Message[]) => void
  addMessage: (message: Message) => void
  updateMessage: (id: string, updater: (msg: Message) => void) => void
  setStreamingMessage: (id: string | null) => void
  setStreamingText: (text: string) => void
  appendStreamingText: (delta: string) => void
  setActiveAttempt: (attemptId: string | null) => void
  cancelAttempt: () => Promise<void>
  loadMessages: (sessionId: string) => Promise<void>
  clearMessages: () => void
}

export const useChatStore = create<ChatState>()(
  immer((set, get) => ({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    activeAttemptId: null,
    setMessages: (messages) =>
      set((state) => {
        state.messages.clear()
        messages.forEach((m) => state.messages.set(m.id, m))
      }),
    addMessage: (message) =>
      set((state) => {
        state.messages.set(message.id, message)
      }),
    updateMessage: (id, updater) =>
      set((state) => {
        const msg = state.messages.get(id)
        if (msg) updater(msg)
      }),
    setStreamingMessage: (id) => set({ streamingMessageId: id }),
    setStreamingText: (text) => set({ streamingText: text }),
    appendStreamingText: (delta) =>
      set((state) => {
        state.streamingText += delta
      }),
    setActiveAttempt: (attemptId) => set({ activeAttemptId: attemptId }),
    cancelAttempt: async () => {
      const { activeAttemptId } = get()
      const sessionId = useSessionStore.getState().currentSessionId
      if (!sessionId) return

      try {
        await api.post('/chat/cancel', {
          session_id: sessionId,
          attempt_id: activeAttemptId,
        })
      } catch (err) {
        console.error('Cancel failed:', err)
      }

      // Clear streaming state
      set((state) => {
        state.streamingMessageId = null
        state.streamingText = ''
        state.activeAttemptId = null
      })
    },
    clearMessages: () =>
      set((state) => {
        state.messages.clear()
        state.streamingMessageId = null
        state.streamingText = ''
        state.activeAttemptId = null
      }),
    loadMessages: async (sessionId: string) => {
      try {
        const data = await api.get<{ messages: Message[] }>(
          `/chat/session/${sessionId}/messages?limit=200`
        )
        set((state) => {
          state.messages.clear()
          data.messages.forEach((m) => state.messages.set(m.id, m))
        })
      } catch (err) {
        console.error('loadMessages error:', err)
      }
    },
  }))
)
