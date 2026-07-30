import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssistantMessage } from '../components/chat/AssistantMessage'
import { useSystemStore } from '../stores/system'
import type { Message } from '../stores/chat'

const baseMsg: Message = {
  id: 'm1',
  session_id: 's1',
  role: 'assistant',
  parts: [{ type: 'text', id: 'test-1', text: '回复内容' }],
  created_at: 1700000000,
  metadata: { model: 'gpt-4o' },
}

function setProvider(provider: string, model = 'default') {
  useSystemStore.setState({
    llm: { provider, model, configured: !!provider },
  })
}

describe('AssistantMessage', () => {
  beforeEach(() => {
    setProvider('') // passthrough by default
  })

  it('renders avatar + label in bubble mode', () => {
    const { container } = render(
      <AssistantMessage message={baseMsg} layout="bubble" />
    )
    // Has Bot avatar circle
    expect(container.querySelector('.rounded-full')).toBeTruthy()
    // Label shows "Agent · gpt-4o"
    expect(screen.getByText(/Agent · gpt-4o/)).toBeTruthy()
  })

  it('renders flat layout without avatar in flat mode', () => {
    const { container } = render(
      <AssistantMessage message={baseMsg} layout="flat" />
    )
    // No avatar circle in flat mode
    expect(container.querySelector('.rounded-full')).toBeFalsy()
    // Label + time visible
    expect(screen.getByText(/Agent · gpt-4o/)).toBeTruthy()
    // Text content visible
    expect(screen.getByText('回复内容')).toBeTruthy()
  })

  it('omits model from label when metadata.model is missing', () => {
    const msgNoModel = { ...baseMsg, metadata: undefined }
    const { rerender } = render(
      <AssistantMessage message={msgNoModel} layout="bubble" />
    )
    expect(screen.getByText('Agent')).toBeTruthy()
    rerender(<AssistantMessage message={msgNoModel} layout="flat" />)
    expect(screen.getByText('Agent')).toBeTruthy()
  })

  it('splits MiniMax <think> tags into ThinkingBlock + content (provider=minimax)', () => {
    setProvider('minimax', 'minimax-M3')
    const msg: Message = {
      ...baseMsg,
      parts: [{ type: 'text', id: 'msg-1', text: '<think>plan content</think>你好' }],
    }
    const { container } = render(
      <AssistantMessage message={msg} layout="flat" />
    )
    // ThinkingBlock rendered
    expect(screen.getByText(/Thought/)).toBeTruthy()
    // Content (without tags) visible
    expect(screen.getByText('你好')).toBeTruthy()
    // Tags themselves should not be visible as plain text
    expect(container.textContent).not.toContain('<think>')
  })

it('does NOT parse thinking when provider has no parser', () => {
    setProvider('unknown-provider')
    const msg: Message = {
      ...baseMsg,
      parts: [{ type: 'text', id: 'msg-2', text: 'plan你好' }],
    }
    render(<AssistantMessage message={msg} layout="flat" />)
    // Tags appear as-is in the content (Markdown renderer shows them)
    // The text content should contain the tags
    expect(screen.getByText(/plan你好/)).toBeTruthy()
  })

  it('renders queued indicator with position/length when isQueued', () => {
    const queuedMsg: Message = {
      ...baseMsg,
      parts: [],
      metadata: {
        queue_status: 'queued',
        queue_position: 2,
        queue_length: 3,
      },
    }
    render(
      <AssistantMessage
        message={queuedMsg}
        isQueued={true}
        layout="bubble"
      />
    )
    expect(screen.getByText(/等待中\.\.\. 2\/3/)).toBeTruthy()
  })
})