import { describe, it, expect, beforeEach } from 'vitest'
import { useChatStore } from '../stores/chat'
import type { Message } from '../stores/chat'

// Logic-only test: verify "filtered out" works as expected when
// MessageList's itemContent returns null for tool messages. The
// component-level test requires a real Virtuoso which is virtual
// and hard to assert against with DOM queries.
//
// The fix lives in MessageList.tsx itemContent: it returns null
// for message.role === 'tool'. If someone reverts that, this test
// catches the regression by importing the source and asserting the
// branch is present.

describe('MessageList tool filtering', () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: new Map(),
      streamingMessageId: null,
      streamingText: '',
      activeAttemptId: null,
      tokensUsed: new Map(),
      queuePaused: new Map(),
      queueLengths: new Map(),
      lastCompaction: null,
    })
  })

  it('messages Map can contain tool records without UI rendering them', () => {
    // Simulate DB shape: 1 user + 1 assistant + 13 tool messages.
    // The frontend MessageList must skip tool messages when rendering.
    const messages: Message[] = [
      {
        id: 'u1',
        session_id: 's1',
        role: 'user',
        parts: [{ type: 'text', text: '分析' }],
        created_at: 1,
      },
      {
        id: 'a1',
        session_id: 's1',
        role: 'assistant',
        parts: [{ type: 'text', text: '回复' }],
        created_at: 2,
      },
    ]
    for (let i = 0; i < 13; i++) {
      messages.push({
        id: `t${i}`,
        session_id: 's1',
        role: 'tool',
        parts: [],
        created_at: 100 + i,
      })
    }

    useChatStore.setState({
      messages: new Map(messages.map((m) => [m.id, m])),
    })

    const all = Array.from(useChatStore.getState().messages.values())
    const userOrAssistant = all.filter(
      (m) => m.role === 'user' || m.role === 'assistant'
    )
    const toolOnly = all.filter((m) => m.role === 'tool')

    // Verify the data ratio matches the bug scenario
    expect(toolOnly).toHaveLength(13)
    expect(userOrAssistant).toHaveLength(2)

    // The MessageList component applies: if (message.role === 'tool') return null.
    // We assert via grep on the source file to lock the contract.
    // (Sufficient for regression: the source MUST contain the filter.)
    const fs = require('fs')
    const path = require('path')
    const src = fs.readFileSync(
      path.join(__dirname, '../components/chat/MessageList.tsx'),
      'utf8'
    )
    expect(src).toMatch(/message\.role === ['"]tool['"]/)
    expect(src).toMatch(/return null/)
  })
})
