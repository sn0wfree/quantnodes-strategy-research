// components/chat/AgentCard — renders an AgentPart inside AssistantMessage.

import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentCard } from '../components/chat/AgentCard'
import type { AgentPart } from '../stores/chat'

function agentPart(overrides: Partial<AgentPart> = {}): AgentPart {
  return {
    type: 'agent',
    id: 'agent-sub-1',
    agentId: 'sub-1',
    name: 'explore',
    status: 'running',
    toolCalls: [],
    streamingText: '',
    startedAt: 1700000000,
    isStreaming: true,
    ...overrides,
  }
}

describe('AgentCard', () => {
  it('shows the agent name and status label', () => {
    render(<AgentCard agentPart={agentPart()} />)
    expect(screen.getByText('explore')).toBeTruthy()
    expect(screen.getByText('执行中')).toBeTruthy()
  })

  it('shows completed status label', () => {
    render(<AgentCard agentPart={agentPart({ status: 'completed', isStreaming: false })} />)
    expect(screen.getByText('完成')).toBeTruthy()
  })

  it('shows failed status label', () => {
    render(<AgentCard agentPart={agentPart({ status: 'failed', isStreaming: false })} />)
    expect(screen.getByText('失败')).toBeTruthy()
  })

  it('shows tool call count summary', () => {
    const part = agentPart({
      status: 'completed',
      toolCalls: [
        { type: 'tool_call', id: 't1', name: 'read_file', status: 'done', arguments: {} },
        { type: 'tool_call', id: 't2', name: 'run_backtest', status: 'done', arguments: {} },
      ],
    })
    render(<AgentCard agentPart={part} />)
    expect(screen.getByText(/2\/2 工具/)).toBeTruthy()
  })

  it('shows error text when failed (after expand)', () => {
    const part = agentPart({ status: 'failed', isStreaming: false, error: 'child boom' })
    const { container } = render(<AgentCard agentPart={part} />)
    expect(screen.queryByText('child boom')).toBeNull()
    fireEvent.click(container.querySelector('[role="button"]')!)
    expect(screen.getByText('child boom')).toBeTruthy()
  })

  it('expands to show streaming text when running', () => {
    const part = agentPart({ status: 'running', streamingText: 'working...' })
    render(<AgentCard agentPart={part} />)
    // Expanded by default while running
    expect(screen.getByText('working...')).toBeTruthy()
  })

  it('collapses tool calls when completed (click to expand)', () => {
    const part = agentPart({
      status: 'completed',
      isStreaming: false,
      toolCalls: [
        { type: 'tool_call', id: 't1', name: 'read_file', status: 'done', arguments: {} },
      ],
    })
    const { container } = render(<AgentCard agentPart={part} />)
    // Completed → collapsed by default; tool name not shown until expand
    expect(screen.queryByText('read_file')).toBeNull()
    fireEvent.click(container.querySelector('[role="button"]')!)
    expect(screen.getByText('read_file')).toBeTruthy()
  })
})
