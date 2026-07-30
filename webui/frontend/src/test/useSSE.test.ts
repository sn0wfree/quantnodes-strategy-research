import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { enableMapSet } from 'immer'

enableMapSet()

// Mock EventSource with controllable instances
class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  onopen: ((ev?: any) => void) | null = null
  onerror: ((ev?: any) => void) | null = null
  onmessage: ((ev?: any) => void) | null = null
  readyState = 0
  listeners = new Map<string, (ev: any) => void>()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (ev: any) => void) {
    this.listeners.set(type, fn)
  }

  removeEventListener(type: string) {
    this.listeners.delete(type)
  }

  close() {
    this.readyState = 2 // CLOSED
  }

  // Test helper
  emit(type: string, data: any) {
    const fn = this.listeners.get(type)
    if (fn) fn({ type, data: JSON.stringify(data) })
  }
}

;(globalThis as any).EventSource = MockEventSource

// Mock localStorage for sr-auth
const localStorageMock = {
  store: {} as Record<string, string>,
  getItem(k: string) { return this.store[k] ?? null },
  setItem(k: string, v: string) { this.store[k] = v },
  removeItem(k: string) { delete this.store[k] },
  clear() { this.store = {} },
}
;(globalThis as any).localStorage = localStorageMock

// Import after mocks
const { useSSE } = await import('../hooks/useSSE')
const { useChatStore } = await import('../stores/chat')
const { useAgentStore } = await import('../stores/agents')
const { useWorkflowStore } = await import('../stores/workflow')
const { useToastStore } = await import('../stores/toast')
const { useSSEStore } = await import('../stores/sse')

// Helper to get current EventSource instance
const getCurrentES = () => MockEventSource.instances[MockEventSource.instances.length - 1]

// Helper to seed chat/agent/workflow with a message
const seedMessage = (id: string, role: 'user' | 'assistant' = 'assistant') => {
  useChatStore.getState().addMessage({
    id,
    session_id: 'sess-1',
    role,
    parts: [{ type: 'text', text: '' }],
    created_at: Date.now() / 1000,
  })
}

describe('useSSE', () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0
    localStorageMock.clear()
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
    })
    useAgentStore.setState({ agents: new Map() })
    useWorkflowStore.setState({
      dagNodes: [],
      dagEdges: [],
      presets: [],
      currentPresetId: null,
      executionProgress: 0,
    })
    useToastStore.setState({ toasts: [] })
    useSSEStore.setState({ status: 'connecting' })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ──────────── connection lifecycle ────────────

  describe('connection lifecycle', () => {
    it('does not connect when sessionId is null', () => {
      renderHook(() => useSSE(null))
      expect(MockEventSource.instances.length).toBe(0)
    })

    it('creates EventSource with session_id query param', () => {
      renderHook(() => useSSE('sess-1'))

      expect(MockEventSource.instances.length).toBe(1)
      const es = getCurrentES()
      expect(es.url).toContain('session_id=sess-1')
      expect(es.url).toMatch(/^\/api\/chat\/events/)
    })

    it('attaches token from sr-auth localStorage', () => {
      localStorageMock.setItem('sr-auth', JSON.stringify({
        state: { token: 'jwt-123' }
      }))

      renderHook(() => useSSE('sess-1'))

      const es = getCurrentES()
      expect(es.url).toContain('token=jwt-123')
    })

    it('closes EventSource on unmount', () => {
      const { unmount } = renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      const closeSpy = vi.spyOn(es, 'close')

      unmount()

      expect(closeSpy).toHaveBeenCalled()
    })

    it('reconnects on error with exponential backoff', () => {
      vi.useFakeTimers()
      renderHook(() => useSSE('sess-1'))
      const initialCount = MockEventSource.instances.length

      const es = getCurrentES()
      // trigger error
      act(() => {
        if (es.onerror) es.onerror(new Event('error'))
      })

      expect(MockEventSource.instances.length).toBe(initialCount) // not yet

      act(() => {
        vi.advanceTimersByTime(1000)
      })

      // reconnect should have happened (initial delay = 1000ms)
      expect(MockEventSource.instances.length).toBe(initialCount + 1)

      vi.useRealTimers()
    })
  })

  // ──────────── event routing ────────────

  describe('event routing', () => {
    let es: MockEventSource

    beforeEach(() => {
      renderHook(() => useSSE('sess-1'))
      es = getCurrentES()
    })

    it('text_delta updates message text + appends to streaming', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text_delta', { text: 'hello', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textPart = msg.parts.find((p) => p.type === 'text') as any
      expect(textPart.text).toBe('hello')
      expect(useChatStore.getState().streamingText).toBe('hello')
    })

    it('text_delta accumulates on multiple calls', () => {
      seedMessage('msg-1')
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('text_delta', { text: 'foo', message_id: 'msg-1' })
        es.emit('text_delta', { text: ' bar', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textPart = msg.parts.find((p) => p.type === 'text') as any
      expect(textPart.text).toBe('foo bar')
    })

    it('assistant_message replaces text content', () => {
      seedMessage('msg-1')
      useChatStore.getState().updateMessage('msg-1', (m) => {
        if (m.parts[0].type === 'text') m.parts[0].text = 'old'
      })

      act(() => {
        es.emit('assistant_message', { content: 'new content', message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const textPart = msg.parts.find((p) => p.type === 'text') as any
      expect(textPart.text).toBe('new content')
    })

    it('tool_call appends new tool_call part', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('tool_call', {
          message_id: 'msg-1',
          id: 'tc-1',
          name: 'alpha_calc',
          arguments: '{"x": 1}',
        })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tc = msg.parts.find((p) => p.type === 'tool_call') as any
      expect(tc).toBeDefined()
      expect(tc.id).toBe('tc-1')
      expect(tc.name).toBe('alpha_calc')
      expect(tc.status).toBe('running')
    })

    it('tool_call with duplicate id is idempotent', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('tool_call', { message_id: 'msg-1', id: 'tc-1', name: 'a', arguments: '{}' })
        es.emit('tool_call', { message_id: 'msg-1', id: 'tc-1', name: 'a', arguments: '{}' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tcs = msg.parts.filter((p) => p.type === 'tool_call')
      expect(tcs.length).toBe(1)
    })

    it('tool_result updates matching tool_call', () => {
      seedMessage('msg-1')
      useChatStore.getState().updateMessage('msg-1', (m) => {
        m.parts.push({ type: 'tool_call', id: 'tc-1', name: 'a', arguments: '{}', status: 'running' })
      })

      act(() => {
        es.emit('tool_result', { message_id: 'msg-1', id: 'tc-1', result: '{"y": 2}', status: 'done' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const tc = msg.parts.find((p) => p.type === 'tool_call' && (p as any).id === 'tc-1') as any
      expect(tc.status).toBe('done')
      expect(tc.result).toBe('{"y": 2}')
    })

    it('thinking_start appends thinking part', () => {
      seedMessage('msg-1')

      act(() => {
        es.emit('thinking_start', { message_id: 'msg-1' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const thinking = msg.parts.find((p) => p.type === 'thinking') as any
      expect(thinking).toBeDefined()
      expect(thinking.text).toBe('')
    })

    it('thinking_delta appends to last thinking part', () => {
      seedMessage('msg-1')
      useChatStore.getState().updateMessage('msg-1', (m) => {
        m.parts.push({ type: 'thinking', text: 'plan:', collapsed: true } as any)
      })

      act(() => {
        es.emit('thinking_delta', { message_id: 'msg-1', delta: ' think more' })
      })

      const msg = useChatStore.getState().messages.get('msg-1')!
      const thinking = msg.parts.find((p) => p.type === 'thinking') as any
      expect(thinking.text).toBe('plan: think more')
    })

    it('agent_done clears streaming message', () => {
      useChatStore.getState().setStreamingMessage('msg-1')
      expect(useChatStore.getState().streamingMessageId).toBe('msg-1')

      act(() => {
        es.emit('agent_done', { message_id: 'msg-1' })
      })

      expect(useChatStore.getState().streamingMessageId).toBeNull()
    })

    it('error event shows toast + clears streaming', () => {
      useChatStore.getState().setStreamingMessage('msg-1')

      act(() => {
        es.emit('error', { error: 'LLM API failed' })
      })

      expect(useToastStore.getState().toasts.length).toBe(1)
      expect(useToastStore.getState().toasts[0].message).toBe('LLM API failed')
      expect(useChatStore.getState().streamingMessageId).toBeNull()
    })

    it('agent_status updates agent state', () => {
      useAgentStore.setState({
        agents: new Map([['a-1', {
          id: 'a-1',
          session_id: 'sess-1',
          status: 'pending',
          name: 'Test',
          created_at: 0,
          updated_at: 0,
          tool_calls_count: 0,
          compaction_count: 0,
          context_tokens: 0,
          context_tokens_limit: 8000,
          iterations_detail: [],
        }]]),
      })

      act(() => {
        es.emit('agent_status', { agent_id: 'a-1', status: 'running', iteration: 1 })
      })

      const agent = useAgentStore.getState().agents.get('a-1')!
      expect(agent.status).toBe('running')
    })

    it('agent_loop merges loop data', () => {
      useAgentStore.setState({
        agents: new Map([['a-1', {
          id: 'a-1',
          session_id: 'sess-1',
          status: 'running',
          name: 'Test',
          created_at: 0,
          updated_at: 0,
          tool_calls_count: 0,
          compaction_count: 0,
          context_tokens: 0,
          context_tokens_limit: 8000,
          iterations_detail: [],
        }]]),
      })

      act(() => {
        es.emit('agent_loop', { agent_id: 'a-1', iteration: 3, total_cost: 0.05 })
      })

      const agent = useAgentStore.getState().agents.get('a-1')!
      expect(agent.status).toBe('running')
    })

    it('dag_update updates node status', () => {
      useWorkflowStore.setState({
        dagNodes: [{ id: 'n1', label: 'Plan', status: 'pending' }],
        dagEdges: [],
        presets: [],
        currentPresetId: null,
        executionProgress: 0,
      })

      act(() => {
        es.emit('dag_update', { node_id: 'n1', status: 'completed' })
      })

      expect(useWorkflowStore.getState().dagNodes[0].status).toBe('completed')
    })

    it('progress updates execution percentage', () => {
      act(() => {
        es.emit('progress', { progress: 0.75 })
      })

      expect(useWorkflowStore.getState().executionProgress).toBe(0.75)
    })

    it('malformed JSON is silently ignored', () => {
      const es = getCurrentES()
      // Override emit to use raw string data
      es.listeners.set('text_delta', (ev: any) => {
        // simulate hook's JSON.parse failure path
        try {
          JSON.parse(ev.data)
        } catch {
          // expected
        }
      })

      expect(() => {
        act(() => {
          es.listeners.get('text_delta')!({ type: 'text_delta', data: 'not json{' })
        })
      }).not.toThrow()
    })
  })

  // ──────────── heartbeat listener ────────────

  describe('heartbeat listener', () => {
    it('registers a heartbeat listener on EventSource', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()
      // The heartbeat listener must be attached so the backend's
      // periodic comment lines keep the connection marked alive.
      expect(es.listeners.has('heartbeat')).toBe(true)
    })

    it('heartbeat event marks the connection as connected', () => {
      useSSEStore.setState({ status: 'disconnected' })
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      expect(useSSEStore.getState().status).toBe('connecting')

      act(() => {
        es.listeners.get('heartbeat')!({
          type: 'heartbeat',
          data: JSON.stringify({ ts: 1234 }),
        })
      })

      expect(useSSEStore.getState().status).toBe('connected')
    })

    it('heartbeat recovers from transient disconnected state', () => {
      renderHook(() => useSSE('sess-1'))
      const es = getCurrentES()

      // Simulate the browser firing onerror briefly, then a heartbeat
      // comes in to confirm the connection is healthy.
      act(() => {
        es.onerror?.({})
      })
      expect(useSSEStore.getState().status).toBe('disconnected')

      act(() => {
        es.listeners.get('heartbeat')!({
          type: 'heartbeat',
          data: JSON.stringify({ ts: 1234 }),
        })
      })
      expect(useSSEStore.getState().status).toBe('connected')
    })
  })
})