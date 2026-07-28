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
    // Single tool call should not show the group header
    expect(screen.queryByText(/个工具调用/)).not.toBeInTheDocument()
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
    expect(screen.getByText(/3 个工具调用/)).toBeInTheDocument()
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
    // Should show counts in header
    expect(screen.getByText('2 完成')).toBeInTheDocument()
    expect(screen.getByText(/运行中/)).toBeInTheDocument()
    expect(screen.getByText('1 失败')).toBeInTheDocument()
  })
})