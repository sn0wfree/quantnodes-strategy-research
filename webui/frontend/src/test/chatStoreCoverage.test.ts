// stores/chat.ts — covers the previously un-tested async and
// derived-state setters: setActiveAttempt, setLastCompaction,
// cancelAttempt, resumeQueue, loadMessages, loadMoreMessages,
// fetchSessionAttempts.

import { describe, it, expect, beforeEach, vi } from 'vitest'

let mockSessionId: string | null = 'sess-1'
vi.mock('../stores/session', () => ({
  useSessionStore: {
    getState: () => ({ currentSessionId: mockSessionId }),
  },
}))

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import { api } from '../api/client'
import { useChatStore } from '../stores/chat'

const mockGet = vi.mocked(api.get)
const mockPost = vi.mocked(api.post)

beforeEach(() => {
  vi.clearAllMocks()
  mockSessionId = 'sess-1'
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    activeAttemptId: null,
    queuePaused: new Map(),
    queueLengths: new Map(),
    lastCompaction: null,
    hasMore: new Map(),
    tokensUsed: new Map(),
    totalTokensSeen: new Map(),
    partTextAccumDelta: {},
  })
})

describe('chat store simple setters', () => {
  it('setActiveAttempt stores the id and null clears it', () => {
    useChatStore.getState().setActiveAttempt('a-1')
    expect(useChatStore.getState().activeAttemptId).toBe('a-1')
    useChatStore.getState().setActiveAttempt(null)
    expect(useChatStore.getState().activeAttemptId).toBeNull()
  })

  it('setLastCompaction writes the layer/timestamp payload', () => {
    useChatStore.getState().setLastCompaction({ layer: 'context', timestamp: 1234 })
    expect(useChatStore.getState().lastCompaction?.layer).toBe('context')
    expect(useChatStore.getState().lastCompaction?.timestamp).toBe(1234)
    useChatStore.getState().setLastCompaction(null)
    expect(useChatStore.getState().lastCompaction).toBeNull()
  })
})

describe('cancelAttempt', () => {
  it('posts to /chat/cancel and clears streaming state', async () => {
    useChatStore.getState().setActiveAttempt('a-1')
    useChatStore.getState().setStreamingMessage('m-1')
    useChatStore.getState().setStreamingText('partial')

    await useChatStore.getState().cancelAttempt()

    expect(mockPost).toHaveBeenCalledWith('/chat/cancel', {
      session_id: 'sess-1',
      attempt_id: 'a-1',
    })
    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBeNull()
    expect(s.streamingText).toBe('')
    expect(s.activeAttemptId).toBeNull()
  })

  it('no-ops without a current session', async () => {
    mockSessionId = null
    await useChatStore.getState().cancelAttempt()
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('still clears streaming state when the API call rejects', async () => {
    mockPost.mockRejectedValueOnce(new Error('cancel boom') as never)
    useChatStore.getState().setStreamingMessage('m-1')
    const consoleErr = vi.spyOn(console, 'error').mockImplementation(() => {})
    await useChatStore.getState().cancelAttempt()
    expect(useChatStore.getState().streamingMessageId).toBeNull()
    expect(consoleErr).toHaveBeenCalled()
    consoleErr.mockRestore()
  })
})

describe('resumeQueue', () => {
  it('posts and unpauses the session queue', async () => {
    useChatStore.getState().setQueuePaused('sess-1', true)
    await useChatStore.getState().resumeQueue()
    expect(mockPost).toHaveBeenCalledWith('/chat/queue/resume', { session_id: 'sess-1' })
    expect(useChatStore.getState().queuePaused.get('sess-1')).toBe(false)
  })

  it('no-ops without a current session', async () => {
    mockSessionId = null
    await useChatStore.getState().resumeQueue()
    expect(mockPost).not.toHaveBeenCalled()
  })
})

describe('loadMessages', () => {
  it('replaces messages and records has_more', async () => {
    mockGet.mockResolvedValueOnce({
      messages: [
        { id: 'm1', session_id: 'sess-1', role: 'user', parts: [], created_at: 100 },
      ],
      has_more: true,
    } as never)
    mockGet.mockResolvedValueOnce({ attempts: [] } as never)
    await useChatStore.getState().loadMessages('sess-1')
    const s = useChatStore.getState()
    expect(s.messages.size).toBe(1)
    expect(s.hasMore.get('sess-1')).toBe(true)
  })

  it('discards stale responses when loadMessages is called again mid-flight', async () => {
    // First call returns slowly; second call returns fast and is the
    // "winner" by sequence number.
    let resolveFirst!: (v: unknown) => void
    mockGet.mockImplementationOnce(
      () => new Promise((r) => { resolveFirst = r as (v: unknown) => void }) as never
    )
    mockGet.mockResolvedValueOnce({
      messages: [{ id: 'newer', session_id: 'sess-1', role: 'user', parts: [], created_at: 200 }],
      has_more: false,
    } as never)
    const p1 = useChatStore.getState().loadMessages('sess-1')
    const p2 = useChatStore.getState().loadMessages('sess-1')
    // Resolve the second GET (the /attempts fetch) so p2 completes.
    await p2
    // Now resolve the first call's GET with a message that should be ignored.
    resolveFirst({ messages: [{ id: 'older', session_id: 'sess-1', role: 'user', parts: [], created_at: 50 }], has_more: true })
    await p1
    expect(useChatStore.getState().messages.has('newer')).toBe(true)
    expect(useChatStore.getState().messages.has('older')).toBe(false)
  })
})

describe('fetchSessionAttempts', () => {
  it('attaches streaming state to running attempts', async () => {
    mockGet.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'a-1',
          message_id: 'm-1',
          status: 'running',
          prompt: '',
          created_at: '2026-08-01T10:00:00',
        },
      ],
    } as never)
    await useChatStore.getState().fetchSessionAttempts('sess-1')
    const s = useChatStore.getState()
    expect(s.activeAttemptId).toBe('a-1')
    expect(s.streamingMessageId).toBe('m-1')
    expect(s.messages.has('m-1')).toBe(true)
  })

  it('marks queued attempts on the placeholder metadata', async () => {
    mockGet.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'a-1', message_id: 'm-1', status: 'queued',
          prompt: '', created_at: '2026-08-01T10:00:00',
        },
        {
          attempt_id: 'a-2', message_id: 'm-2', status: 'queued',
          prompt: '', created_at: '2026-08-01T10:00:01',
        },
      ],
    } as never)
    await useChatStore.getState().fetchSessionAttempts('sess-1')
    const s = useChatStore.getState()
    const m1 = s.messages.get('m-1')!
    expect(m1.metadata?.queue_status).toBe('queued')
    expect(m1.metadata?.queue_position).toBe(1)
    expect(m1.metadata?.queue_length).toBe(2)
    expect(s.activeAttemptId).toBeNull()
  })

  it('degrades gracefully on API failure', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom') as never)
    const consoleErr = vi.spyOn(console, 'error').mockImplementation(() => {})
    await useChatStore.getState().fetchSessionAttempts('sess-1')
    expect(consoleErr).toHaveBeenCalled()
    consoleErr.mockRestore()
  })
})

describe('loadMoreMessages', () => {
  it('no-ops when the session has no messages yet', async () => {
    await useChatStore.getState().loadMoreMessages('sess-1')
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('fetches older messages using the earliest created_at as cursor', async () => {
    useChatStore.setState((s) => {
      const m = new Map(s.messages)
      m.set('m-old', {
        id: 'm-old', session_id: 'sess-1', role: 'user', parts: [], created_at: 100,
      })
      return { messages: m }
    })
    mockGet.mockResolvedValueOnce({
      messages: [
        { id: 'm-older', session_id: 'sess-1', role: 'user', parts: [], created_at: 50 },
      ],
      has_more: false,
    } as never)
    await useChatStore.getState().loadMoreMessages('sess-1')
    expect(mockGet).toHaveBeenCalledWith(expect.stringContaining('before=100'))
    expect(useChatStore.getState().messages.has('m-older')).toBe(true)
  })

  it('skips messages that do not belong to the requested session', async () => {
    useChatStore.setState((s) => {
      const m = new Map(s.messages)
      m.set('m-other', {
        id: 'm-other', session_id: 'sess-2', role: 'user', parts: [], created_at: 100,
      })
      return { messages: m }
    })
    await useChatStore.getState().loadMoreMessages('sess-1')
    expect(mockGet).not.toHaveBeenCalled()
  })
})