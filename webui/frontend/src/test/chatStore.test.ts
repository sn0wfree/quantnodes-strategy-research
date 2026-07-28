import { describe, it, expect, beforeEach } from 'vitest'
import { useChatStore } from '../stores/chat'
import type { Message } from '../stores/chat'
import { enableMapSet } from 'immer'

enableMapSet()

describe('useChatStore', () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
    })
  })

  it('adds a message', () => {
    const msg: Message = {
      id: 'msg-1',
      session_id: 'sess-1',
      role: 'user',
      parts: [{ type: 'text', text: 'Hello' }],
      created_at: Date.now() / 1000,
    }

    useChatStore.getState().addMessage(msg)

    expect(useChatStore.getState().messages.get('msg-1')).toEqual(msg)
    expect(useChatStore.getState().messages.size).toBe(1)
  })

  it('updates a message', () => {
    const msg: Message = {
      id: 'msg-2',
      session_id: 'sess-1',
      role: 'assistant',
      parts: [{ type: 'text', text: 'Initial' }],
      created_at: Date.now() / 1000,
    }

    const store = useChatStore.getState()
    store.addMessage(msg)
    store.updateMessage('msg-2', (m) => {
      if (m.parts[0].type === 'text') {
        m.parts[0].text = 'Updated'
      }
    })

    const updated = useChatStore.getState().messages.get('msg-2')
    expect(updated?.parts[0]).toMatchObject({ type: 'text', text: 'Updated' })
  })

  it('appends streaming text', () => {
    const store = useChatStore.getState()
    store.appendStreamingText('hello')
    store.appendStreamingText(' world')

    expect(useChatStore.getState().streamingText).toBe('hello world')
  })

  it('sets streaming message id', () => {
    const store = useChatStore.getState()
    store.setStreamingMessage('msg-3')
    expect(useChatStore.getState().streamingMessageId).toBe('msg-3')

    store.setStreamingMessage(null)
    expect(useChatStore.getState().streamingMessageId).toBeNull()
  })

  it('sets messages (replaces all)', () => {
    const messages: Message[] = [
      {
        id: 'm1', session_id: 's', role: 'user',
        parts: [{ type: 'text', text: 'a' }], created_at: 1,
      },
      {
        id: 'm2', session_id: 's', role: 'assistant',
        parts: [{ type: 'text', text: 'b' }], created_at: 2,
      },
    ]

    useChatStore.getState().setMessages(messages)
    expect(useChatStore.getState().messages.size).toBe(2)
  })
})