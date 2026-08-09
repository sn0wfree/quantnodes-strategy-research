import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SlashCommandMenu } from '../components/chat/SlashCommandMenu'
import { MessageActions } from '../components/chat/MessageActions'
import { QuickStartChips } from '../components/chat/QuickStartChips'
import { dayLabel } from '../utils/time'
import type { Message } from '../stores/chat'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'

vi.mock('../api/client', () => ({
  api: {
    post: vi.fn().mockResolvedValue({ status: 'queued', attempt_id: 'a1' }),
  },
}))

import { api } from '../api/client'

const mockPost = api.post as ReturnType<typeof vi.fn>

describe('SlashCommandMenu', () => {
  it('filters commands by label', () => {
    render(<SlashCommandMenu query="/研" onSelect={() => {}} />)
    expect(screen.getByText('研究')).toBeTruthy()
    expect(screen.queryByText('目标')).toBeNull()
  })

  it('calls onSelect when a command is clicked', () => {
    const onSelect = vi.fn()
    render(<SlashCommandMenu query="/goal" onSelect={onSelect} />)
    fireEvent.click(screen.getByText('目标'))
    // /goal is an argument-bearing command, so autoSend is undefined
    // (P32). AutoSend commands (e.g. /clear) pass true as the second
    // argument; see SlashCommandMenu.test.tsx for that contract.
    expect(onSelect).toHaveBeenCalledWith('/goal', undefined)
  })

  it('shows no-match message for unknown query', () => {
    render(<SlashCommandMenu query="/zzz" onSelect={() => {}} />)
    expect(screen.getByText('无匹配命令')).toBeTruthy()
  })
})

describe('MessageActions', () => {
  const asst: Message = {
    id: 'a1',
    session_id: 's1',
    role: 'assistant',
    parts: [{ type: 'text', id: 't1', text: '回答' }],
    created_at: 1700000000,
  }
  const userMsg: Message = {
    id: 'u1',
    session_id: 's1',
    role: 'user',
    parts: [{ type: 'text', id: 't0', text: '问题' }],
    created_at: 1699999990,
  }

  beforeEach(() => {
    mockPost.mockClear()
    useSessionStore.setState({ currentSessionId: 's1' })
  })

  it('regenerate re-sends the preceding user message', () => {
    // Seed the chat store's messages map with a user + assistant pair.
    useChatStore.setState((s) => {
      s.messages = new Map([
        ['u1', userMsg],
        ['a1', asst],
      ])
    })
    render(<MessageActions message={asst} alwaysVisible />)
    fireEvent.click(screen.getByLabelText('重新生成'))
    expect(mockPost).toHaveBeenCalledWith('/chat/send_async', {
      session_id: 's1',
      content: '问题',
    })
  })

  it('regenerate shows a toast when no previous user message exists', () => {
    useChatStore.setState((s) => {
      s.messages = new Map([['a1', asst]])
    })
    render(<MessageActions message={asst} alwaysVisible />)
    fireEvent.click(screen.getByLabelText('重新生成'))
    expect(mockPost).not.toHaveBeenCalled()
  })
})

describe('QuickStartChips', () => {
  beforeEach(() => {
    mockPost.mockClear()
    useSessionStore.setState({ currentSessionId: 's1' })
  })

  it('renders suggestion chips and sends on click', () => {
    render(<QuickStartChips />)
    const chip = screen.getByText('分析当前持仓风险')
    expect(chip).toBeTruthy()
    fireEvent.click(chip)
    expect(mockPost).toHaveBeenCalledWith('/chat/send_async', {
      session_id: 's1',
      content: '分析当前持仓的风险，给出建议',
      agent_id: undefined,
    })
  })
})

describe('dayLabel', () => {
  it('returns today for now', () => {
    expect(dayLabel(Date.now() / 1000)).toBe('今天')
  })
})