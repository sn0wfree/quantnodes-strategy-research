// hooks/sse/messageHandlers — tool_call / tool_progress / tool_result /
// assistant_message covering the assistant turn assembly pipeline.

import { describe, it, expect, beforeEach } from 'vitest'
import {
  toolCall,
  toolProgress,
  toolResult,
  assistantMessage,
} from '../hooks/sse/messageHandlers'
import { useChatStore } from '../stores/chat'
import type { SSEContext } from '../hooks/sse/types'

function ctx(overrides: Partial<SSEContext> = {}): SSEContext {
  const store = useChatStore.getState()
  return {
    sessionId: 'sess-1',
    state: {
      hasSeenTotalTokens: () => false,
      getMessages: () => Array.from(store.messages.values()),
      getMessage: (id) => store.messages.get(id),
      isQueuePaused: () => false,
      getTokensUsed: () => 0,
    },
    addMessage: store.addMessage,
    updateMessage: store.updateMessage,
    setStreamingMessage: () => {},
    setStreamingText: () => {},
    appendStreamingText: () => {},
    setActiveAttempt: () => {},
    setQueuePaused: () => {},
    setQueueLength: () => {},
    setTokensUsed: () => {},
    markTotalTokensSeen: () => {},
    setLastCompaction: () => {},
    accumulatePartText: () => {},
    clearPartAccum: () => {},
    updateAgent: () => {},
    updateNodeStatus: () => {},
    setExecutionProgress: () => {},
    setGoal: () => {},
    updateGoal: () => {},
    addToast: () => {},
    patchSessionMeta: () => {},
    ...overrides,
  } as SSEContext
}

function seedAssistant(msgId: string): void {
  useChatStore.setState((s) => {
    const m = new Map(s.messages)
    m.set(msgId, {
      id: msgId,
      session_id: 'sess-1',
      role: 'assistant',
      parts: [],
      created_at: 100,
    })
    return { messages: m }
  })
}

beforeEach(() => {
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    partTextAccumDelta: {},
  })
})

describe('toolCall', () => {
  it('appends a running tool_call part', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'read_file', arguments: '{"path":"x"}' }, ctx())
    const m = useChatStore.getState().messages.get('m1')!
    expect(m.parts).toHaveLength(1)
    const p = m.parts[0] as { type: string; status: string; isStreaming: boolean }
    expect(p.type).toBe('tool_call')
    expect(p.status).toBe('running')
    expect(p.isStreaming).toBe(true)
  })

  it('re-marks an existing tool_call part as streaming on replay', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'read_file', arguments: '{}' }, ctx())
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'read_file', arguments: '{}' }, ctx())
    expect(useChatStore.getState().messages.get('m1')!.parts).toHaveLength(1)
  })

  it('serializes non-string arguments to JSON', () => {
    seedAssistant('m1')
    toolCall(
      { message_id: 'm1', id: 'tc-1', name: 'fn', arguments: { path: 'p' } },
      ctx()
    )
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as {
      arguments: string
    }
    expect(p.arguments).toBe('{"path":"p"}')
  })

  it('no-ops without message_id', () => {
    toolCall({ id: 'tc-1', name: 'fn', arguments: '{}' }, ctx())
    expect(useChatStore.getState().messages.size).toBe(0)
  })
})

describe('toolProgress', () => {
  it('attaches a step list to the matching tool_call part', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'fn', arguments: '{}' }, ctx())
    toolProgress(
      { message_id: 'm1', id: 'tc-1', steps: ['loading', 'parsing'] },
      ctx()
    )
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as {
      progress: string[]
    }
    expect(p.progress).toEqual(['loading', 'parsing'])
  })

  it('no-ops without a steps array', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'fn', arguments: '{}' }, ctx())
    toolProgress({ message_id: 'm1', id: 'tc-1' }, ctx())
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as {
      progress?: string[]
    }
    expect(p.progress).toBeUndefined()
  })
})

describe('toolResult', () => {
  it('marks the tool_call done with serialized result', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'fn', arguments: '{}' }, ctx())
    toolResult(
      { message_id: 'm1', id: 'tc-1', result: { ok: true }, status: 'done' },
      ctx()
    )
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as {
      status: string; result: string; isStreaming: boolean
    }
    expect(p.status).toBe('done')
    expect(p.result).toBe('{"ok":true}')
    expect(p.isStreaming).toBe(false)
  })

  it('preserves a string result as-is', () => {
    seedAssistant('m1')
    toolCall({ message_id: 'm1', id: 'tc-1', name: 'fn', arguments: '{}' }, ctx())
    toolResult(
      { message_id: 'm1', id: 'tc-1', result: 'plain text', status: 'done' },
      ctx()
    )
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as { result: string }
    expect(p.result).toBe('plain text')
  })

  it('no-ops when the tool_call part is not found', () => {
    seedAssistant('m1')
    toolResult({ message_id: 'm1', id: 'tc-missing', result: 'x', status: 'done' }, ctx())
    expect(useChatStore.getState().messages.get('m1')!.parts).toEqual([])
  })
})

describe('assistantMessage', () => {
  it('overwrites the last text part when content is longer (avoids max_iter wipe)', () => {
    seedAssistant('m1')
    useChatStore.getState().updateMessage('m1', (m) => {
      m.parts.push({ type: 'text', id: 't-1', text: 'partial' } as never)
    })
    assistantMessage(
      { message_id: 'm1', content: 'partial and more text', message_type: 'final' },
      ctx()
    )
    const p = useChatStore.getState().messages.get('m1')!.parts[0] as { text: string }
    expect(p.text).toBe('partial and more text')
  })

  it('creates an error bubble when message_type is "error" and the message is missing', () => {
    assistantMessage(
      {
        message_id: 'm-err',
        content: 'model timeout',
        message_type: 'error',
        metadata: { details: 'OpenAI 5xx' },
      },
      ctx()
    )
    const m = useChatStore.getState().messages.get('m-err')!
    expect(m.message_type).toBe('error')
    expect(m.metadata?.status).toBe('error')
    expect(m.metadata?.details).toBe('OpenAI 5xx')
    expect((m.parts[0] as { text: string }).text).toBe('model timeout')
  })

  it('patches an existing message in place when it is an error', () => {
    seedAssistant('m-err')
    assistantMessage(
      {
        message_id: 'm-err',
        content: 'model timeout',
        message_type: 'error',
      },
      ctx()
    )
    const m = useChatStore.getState().messages.get('m-err')!
    expect(m.message_type).toBe('error')
    expect(m.metadata?.status).toBe('error')
  })

  it('no-ops without content or messageId', () => {
    assistantMessage({}, ctx())
    expect(useChatStore.getState().messages.size).toBe(0)
  })
})