import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useChatStore, type Message } from '../stores/chat'
import { enableMapSet } from 'immer'

enableMapSet()

vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}))

import { api } from '../api/client'
const mockedApi = api as unknown as { get: ReturnType<typeof vi.fn> }

function makeMessage(id: string, role: 'user' | 'assistant', createdAt: number): Message {
  return {
    id,
    session_id: 'sess-1',
    role,
    parts: [{ type: 'text' as const, id: `seed-${id}`, text: 'x' }],
    created_at: createdAt,
  }
}

describe('fetchSessionAttempts (reload recovery)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
      activeAttemptId: null,
      partTextAccumDelta: {},
    })
  })

  it('restores streaming state for a running attempt whose message is materialized', async () => {
    useChatStore.getState().setMessages([makeMessage('asm-1', 'assistant', 100)])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-1',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-1')
    expect(s.activeAttemptId).toBe('att-1')
    expect(s.messages.has('asm-1')).toBe(true)
    // The materialized message keeps its persisted parts
    expect(s.messages.get('asm-1')?.parts).toHaveLength(1)
  })

  it('creates a placeholder when the running message is not yet materialized', async () => {
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-fresh',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-fresh')
    const msg = s.messages.get('asm-fresh')
    expect(msg).toBeDefined()
    expect(msg?.role).toBe('assistant')
    // No queue metadata on a running placeholder
    expect(msg?.metadata?.queue_status).toBeUndefined()
  })

  it('rebuilds queued placeholders with position/length', async () => {
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-running',
          status: 'running',
          prompt: 'first',
          created_at: '2026-01-01T00:00:00',
        },
        {
          attempt_id: 'att-2',
          message_id: 'asm-queued',
          status: 'queued',
          prompt: 'second',
          created_at: '2026-01-01T00:00:01',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBe('asm-running')
    const queued = s.messages.get('asm-queued')
    expect(queued?.metadata).toMatchObject({
      queue_status: 'queued',
      queue_position: 2,
      queue_length: 2,
    })
  })

  it('does nothing when there are no active attempts', async () => {
    mockedApi.get.mockResolvedValueOnce({ attempts: [] })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    expect(s.streamingMessageId).toBeNull()
    expect(s.activeAttemptId).toBeNull()
    expect(s.messages.size).toBe(0)
  })

  it('does not clobber existing queued message metadata on reload', async () => {
    useChatStore
      .getState()
      .setMessages([
        { ...makeMessage('asm-queued', 'assistant', 100), metadata: { queue_status: 'queued', queue_position: 2, queue_length: 2 } },
      ])
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-2',
          message_id: 'asm-queued',
          status: 'queued',
          prompt: 'second',
          created_at: '2026-01-01T00:00:01',
        },
      ],
    })

    await useChatStore.getState().fetchSessionAttempts('sess-1')

    const s = useChatStore.getState()
    const queued = s.messages.get('asm-queued')
    expect(queued?.metadata).toMatchObject({
      queue_status: 'queued',
      queue_position: 1,
      queue_length: 1,
    })
    // single queued attempt → position updates to 1
    expect(useChatStore.getState().messages.size).toBe(1)
  })

  it('loadMessages re-attaches streaming state after a reload', async () => {
    mockedApi.get.mockResolvedValueOnce({
      messages: [makeMessage('asm-1', 'assistant', 100)],
      has_more: false,
    })
    mockedApi.get.mockResolvedValueOnce({
      attempts: [
        {
          attempt_id: 'att-1',
          message_id: 'asm-1',
          status: 'running',
          prompt: 'hi',
          created_at: '2026-01-01T00:00:00',
        },
      ],
    })

    await useChatStore.getState().loadMessages('sess-1')

    expect(mockedApi.get).toHaveBeenCalledTimes(2)
    expect(useChatStore.getState().streamingMessageId).toBe('asm-1')
    expect(useChatStore.getState().activeAttemptId).toBe('att-1')
  })
})
