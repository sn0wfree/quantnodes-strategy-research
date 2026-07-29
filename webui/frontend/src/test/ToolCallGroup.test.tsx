import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ToolCallGroup } from '../components/chat/ToolCallGroup'
import type { ToolCallPart } from '../stores/chat'

const mockToolCall = (id: string, status: ToolCallPart['status']): ToolCallPart => ({
  type: 'tool_call',
  id,
  name: `tool_${id}`,
  arguments: '{"path":"/tmp/test.txt"}',
  status,
})

describe('ToolCallGroup', () => {
  it('renders single tool call directly without group header', () => {
    render(<ToolCallGroup toolCalls={[mockToolCall('1', 'done')]} />)
    // Single tool call should not show the "N tool calls" group header
    expect(screen.queryByText(/tool calls/)).not.toBeInTheDocument()
  })

  it('renders group header for multiple tool calls', () => {
    render(
      <ToolCallGroup
        toolCalls={[
          mockToolCall('1', 'done'),
          mockToolCall('2', 'done'),
          mockToolCall('3', 'running'),
        ]}
      />
    )
    // Group header should show count
    expect(screen.getByText(/3 tool calls/)).toBeInTheDocument()
  })

  it('returns null for empty tool calls', () => {
    const { container } = render(<ToolCallGroup toolCalls={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows summary stats for running/done/error counts', () => {
    render(
      <ToolCallGroup
        toolCalls={[
          mockToolCall('1', 'done'),
          mockToolCall('2', 'done'),
          mockToolCall('3', 'running'),
          mockToolCall('4', 'error'),
        ]}
      />
    )
    expect(screen.getByText('2 done')).toBeInTheDocument()
    expect(screen.getByText(/running/)).toBeInTheDocument()
    expect(screen.getByText('1 failed')).toBeInTheDocument()
  })
})