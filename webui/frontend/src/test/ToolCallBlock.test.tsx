import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolCallBlock } from '../components/chat/ToolCallBlock'
import type { ToolCallPart } from '../stores/chat'

// react-syntax-highlighter is mocked in test/setup.ts
const baseTc: ToolCallPart = {
  type: 'tool_call',
  id: 'tc1',
  name: 'search_alpha',
  arguments: JSON.stringify({ query: 'momentum', top_k: 10 }),
  status: 'done',
  result: JSON.stringify({ results: [{ name: 'alpha1', ic: 0.045 }] }),
}

describe('ToolCallBlock', () => {
  it('renders single-line summary chip with name + args preview', () => {
    render(<ToolCallBlock toolCall={baseTc} />)
    expect(screen.getByText('search_alpha')).toBeTruthy()
    // Args preview shows "query: momentum, top_k: 10"
    expect(screen.getByText(/query: momentum/)).toBeTruthy()
    // Result not visible until expanded
    expect(screen.queryByText(/alpha1/)).toBeNull()
  })

  it('shows markdown-rendered args + result when expanded', () => {
    render(<ToolCallBlock toolCall={baseTc} />)
    // Click the summary chip
    const summary = screen.getByRole('button', { name: /search_alpha/ }).closest('[role="button"]') as HTMLElement
    fireEvent.click(summary)
    // Now "Arguments" and "Result" should be rendered as markdown
    expect(screen.getByText(/Arguments/)).toBeTruthy()
    expect(screen.getByText(/Result/)).toBeTruthy()
  })

  it('handles invalid JSON args gracefully', () => {
    const bad: ToolCallPart = {
      ...baseTc,
      arguments: 'not json{{',
      result: undefined,
    }
    render(<ToolCallBlock toolCall={bad} />)
    // Should not throw; preview shows raw text truncated
    expect(screen.getByText('search_alpha')).toBeTruthy()
  })

  it('shows spinner when status is running', () => {
    const running: ToolCallPart = { ...baseTc, status: 'running' }
    const { container } = render(<ToolCallBlock toolCall={running} />)
    // Loader2 has animate-spin class on its container icon
    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeTruthy()
  })
})
describe('ToolCallBlock — run_backtest summary', () => {
  const rbTc: ToolCallPart = {
    type: 'tool_call',
    id: 'tc-rb',
    name: 'run_backtest',
    arguments: JSON.stringify({ strategy_name: 'a_share_momentum_v4' }),
    status: 'done',
    result: JSON.stringify({
      status: 'ok',
      run: 'run_0002',
      metrics: {
        ann_return: 0.1276,
        sharpe: 0.9185,
        max_drawdown: -0.14,
        calmar: 0.9,
      },
    }),
  }

  it('summarizes nested metrics (ann_return/sharpe/max_drawdown)', () => {
    render(<ToolCallBlock toolCall={rbTc} />)
    expect(screen.getByText(/Sharpe=0\.92/)).toBeTruthy()
    expect(screen.getByText(/MaxDD=-14\.00%/)).toBeTruthy()
    expect(screen.getByText(/年化=12\.76%/)).toBeTruthy()
  })
})
