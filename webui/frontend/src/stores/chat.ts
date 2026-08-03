import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'
import { enableMapSet } from 'immer'
import { api } from '../api/client'
import { shouldInsertSpaceBetween } from '../utils/mastraSmoothStream'
import { useSessionStore } from './session'

// Required for immer to handle Map/Set types in chat state
enableMapSet()

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool' | 'compaction'

export type MessageType = 'user' | 'assistant' | 'tool' | 'compaction' | 'error'

export interface TextPart {
  type: 'text'
  id: string
  text: string
  /** True while the part is still receiving text_delta events. */
  isStreaming?: boolean
}

export interface ToolCallPart {
  type: 'tool_call'
  id: string
  name: string
  arguments: string | unknown
  result?: string | unknown
  status: 'pending' | 'running' | 'done' | 'error'
  progress?: string[]
  /** True while the tool is still being awaited (tool_call → tool_result). */
  isStreaming?: boolean
}

export interface ThinkingPart {
  type: 'thinking'
  text: string
  collapsed?: boolean
  /** True while the reasoning block is still receiving thinking_delta events. */
  isStreaming?: boolean
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
  message_type?: MessageType
  metadata?: {
    model?: string
    tokens_used?: number
    iteration?: number
    queue_position?: number
    queue_length?: number
    queue_status?: 'processing' | 'queued'
    status?: string
    details?: string
  }
}

interface ChatState {
  messages: Map<string, Message>
  streamingMessageId: string | null
  streamingText: string
  activeAttemptId: string | null
  /** Per-session flag: true when the consumer queue was paused after cancel. */
  queuePaused: Map<string, boolean>
  /** Per-session current queue length snapshot. */
  queueLengths: Map<string, number>
  /**
   * Per-session current context-window occupancy (bounded token count,
   * from session_total_tokens.context_used). Drives ContextUsageBar.
   * Reflects compaction: drops after the context is compressed.
   */
  tokensUsed: Map<string, number>
  /**
   * Per-session flag: true once `session_total_tokens` has been seen.
   * `session_total_tokens` is the backend-authoritative value; per-call
   * `llm_usage` deltas must NOT be applied on top of it (double
   * counting). `llm_usage` only acts as a fallback for sessions that
   * never saw the authoritative event.
   */
  totalTokensSeen: Map<string, boolean>
  /** Most recent compaction event (for CompactBanner). */
  lastCompaction: { layer: string; timestamp: number } | null
  setLastCompaction: (c: { layer: string; timestamp: number } | null) => void
  /**
   * Per-part streaming text preview buffer.
   *
   * Modeled after opencode's `part_text_accum_delta`: every text_delta
   * / thinking_delta / tool.input_delta event lands here keyed by
   * partId, on top of the persistent part.text update. `readPartText`
   * prefers the accum buffer (so the first character of a new part
   * becomes visible the instant it arrives) and falls back to the
   * persistent text once the part is sealed (text.ended /
   * thinking_end / tool_result).
   *
   * Cleared by `clearPartAccum` when the corresponding part transitions
   * to a terminal state, and wholesale by `clearMessages` /
   * `setMessages`.
   */
  partTextAccumDelta: Record<string, string>
  accumulatePartText: (partId: string, delta: string) => void
  clearPartAccum: (partId: string) => void
  clearAllPartAccum: () => void
  setMessages: (messages: Message[]) => void
  addMessage: (message: Message) => void
  updateMessage: (id: string, updater: (msg: Message) => void) => void
  setStreamingMessage: (id: string | null) => void
  setStreamingText: (text: string) => void
  appendStreamingText: (delta: string) => void
  setActiveAttempt: (attemptId: string | null) => void
  cancelAttempt: () => Promise<void>
  resumeQueue: () => Promise<void>
  setQueuePaused: (sessionId: string, paused: boolean) => void
  setQueueLength: (sessionId: string, length: number) => void
  setTokensUsed: (sessionId: string, tokens: number) => void
  markTotalTokensSeen: (sessionId: string) => void
  /** Per-session flag: older messages exist beyond the loaded window. */
  hasMore: Map<string, boolean>
  setHasMore: (sessionId: string, more: boolean) => void
  /** Load the next older page (before the earliest loaded message). */
  loadMoreMessages: (sessionId: string) => Promise<void>
  loadMessages: (sessionId: string) => Promise<void>
  clearMessages: () => void
}

// Bump on every loadMessages call so stale responses are dropped
// (rapid session switching must not let an old response overwrite
// the currently viewed session's messages).
let loadMessagesSeq = 0

export const useChatStore = create<ChatState>()(
  immer((set, get) => ({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    activeAttemptId: null,
    queuePaused: new Map(),
    queueLengths: new Map(),
    tokensUsed: new Map(),
    totalTokensSeen: new Map(),
    lastCompaction: null,
    partTextAccumDelta: {},
    setLastCompaction: (c) => set({ lastCompaction: c }),
    accumulatePartText: (partId, delta) =>
      set((state) => {
        if (!delta) return
        const prev = state.partTextAccumDelta[partId] ?? ''
        // Chunk-boundary space recovery — see
        // ``shouldInsertSpaceBetween`` in utils/mastraSmoothStream.
        // DeepSeek-V4-Flash streams one BPE token per SSE chunk and
        // drops the leading space of each. We re-insert one here so
        // ``partTextAccumDelta`` carries the words the user expects
        // to see. The persisted backend ``part.text`` is *not* touched
        // — it stays raw.
        state.partTextAccumDelta[partId] = shouldInsertSpaceBetween(prev, delta)
          ? prev + ' ' + delta
          : prev + delta
      }),
    clearPartAccum: (partId) =>
      set((state) => {
        if (partId in state.partTextAccumDelta) {
          delete state.partTextAccumDelta[partId]
        }
      }),
    clearAllPartAccum: () =>
      set((state) => {
        state.partTextAccumDelta = {}
      }),
    setMessages: (messages) =>
      set((state) => {
        state.messages.clear()
        messages.forEach((m) => state.messages.set(m.id, m))
        state.partTextAccumDelta = {}
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
    resumeQueue: async () => {
      const sessionId = useSessionStore.getState().currentSessionId
      if (!sessionId) return
      try {
        await api.post('/chat/queue/resume', { session_id: sessionId })
        set((state) => {
          state.queuePaused.set(sessionId, false)
        })
      } catch (err) {
        console.error('Resume queue failed:', err)
      }
    },
    setQueuePaused: (sessionId, paused) =>
      set((state) => {
        state.queuePaused.set(sessionId, paused)
      }),
    setQueueLength: (sessionId, length) =>
      set((state) => {
        state.queueLengths.set(sessionId, length)
      }),
    setTokensUsed: (sessionId, tokens) =>
      set((state) => {
        state.tokensUsed.set(sessionId, tokens)
      }),
    markTotalTokensSeen: (sessionId) =>
      set((state) => {
        state.totalTokensSeen.set(sessionId, true)
      }),
    hasMore: new Map(),
    setHasMore: (sessionId, more) =>
      set((state) => {
        state.hasMore.set(sessionId, more)
      }),
    clearMessages: () =>
      set((state) => {
        state.messages.clear()
        state.streamingMessageId = null
        state.streamingText = ''
        state.activeAttemptId = null
        state.queuePaused.clear()
        state.queueLengths.clear()
        state.tokensUsed.clear()
        state.totalTokensSeen.clear()
        state.hasMore.clear()
        state.partTextAccumDelta = {}
      }),
    loadMessages: async (sessionId: string) => {
      const seq = ++loadMessagesSeq
      try {
        const data = await api.get<{ messages: Message[]; has_more?: boolean }>(
          `/chat/session/${sessionId}/messages?limit=200`
        )
        if (seq !== loadMessagesSeq) return // stale response (session switched)
        set((state) => {
          state.messages.clear()
          data.messages.forEach((m) => state.messages.set(m.id, m))
          state.hasMore.set(sessionId, !!data.has_more)
          // History load brings persisted parts back; any preview buffer
          // from a previous live stream is now stale.
          state.partTextAccumDelta = {}
        })
      } catch (err) {
        console.error('loadMessages error:', err)
      }
    },
    loadMoreMessages: async (sessionId: string) => {
      const { messages } = useChatStore.getState()
      // Find the earliest loaded message (oldest created_at) as cursor
      let earliest: Message | null = null
      for (const m of messages.values()) {
        if (m.session_id !== sessionId) continue
        if (!earliest || m.created_at < earliest.created_at) earliest = m
      }
      if (!earliest) return
      try {
        const data = await api.get<{ messages: Message[]; has_more?: boolean }>(
          `/chat/session/${sessionId}/messages?limit=200&before=${earliest.created_at}`
        )
        set((state) => {
          data.messages.forEach((m) => state.messages.set(m.id, m))
          state.hasMore.set(sessionId, !!data.has_more)
        })
      } catch (err) {
        console.error('loadMoreMessages error:', err)
      }
    },
  }))
)
