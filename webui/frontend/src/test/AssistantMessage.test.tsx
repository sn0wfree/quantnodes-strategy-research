import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssistantMessage } from '../components/chat/AssistantMessage'
import type { Message } from '../stores/chat'

const baseMsg: Message = {
  id: 'm1',
  session_id: 's1',
  role: 'assistant',
  parts: [{ type: 'text', text: '回复内容' }],
  created_at: 1700000000,
  metadata: { model: 'gpt-4o' },
}

describe('AssistantMessage', () => {
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
})