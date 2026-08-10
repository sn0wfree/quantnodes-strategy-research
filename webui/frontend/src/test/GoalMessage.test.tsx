// GoalMessage — chat-stream card for message_type='goal' full-snapshot
// messages (backend goal_updated events, docs/goal-events-panel-link.md).

import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { GoalMessage } from '../components/chat/GoalMessage'
import type { Message } from '../stores/chat'

function goalMessage(overrides: Partial<Message['metadata']> = {}): Message {
  return {
    id: 'goal-abc123',
    session_id: 'sess-1',
    role: 'system',
    parts: [],
    created_at: 1750000000,
    message_type: 'goal',
    metadata: {
      goal_id: 'g-1',
      change_type: 'evidence',
      objective: '评估动量因子在 A 股的有效性',
      progress_percent: 45,
      goal_status: 'active',
      evidence_count: 3,
      evidence_text: '截面 IC = 0.045，这是一段很长的证据文本用于测试完整展示与折叠行为',
      criteria: [
        { criterion_id: 'c1', text: '完成截面 IC 分析', status: 'covered', evidence_count: 2 },
        { criterion_id: 'c2', text: '完成分层回测', status: 'in_progress', evidence_count: 1 },
      ],
      ...overrides,
    },
  }
}

describe('GoalMessage', () => {
  it('shows the change label and progress bar', () => {
    render(<GoalMessage message={goalMessage()} />)
    expect(screen.getByText('添加证据')).toBeDefined()
    expect(screen.getByText('45%')).toBeDefined()
    expect(screen.getByText('3 条证据')).toBeDefined()
  })

  it('shows "创建目标" for create changes', () => {
    render(
      <GoalMessage message={goalMessage({ change_type: 'create', objective: '建一个目标' })} />,
    )
    expect(screen.getByText('创建目标')).toBeDefined()
  })

  it('shows "完成目标" + recap for completed goals', () => {
    render(
      <GoalMessage
        message={goalMessage({
          change_type: 'complete',
          goal_status: 'complete',
          recap: 'Sharpe 1.4 达成',
        })}
      />,
    )
    expect(screen.getByText('完成目标')).toBeDefined()
  })

  it('collapses criteria and full evidence by default, expands on click', () => {
    render(<GoalMessage message={goalMessage()} />)
    // Collapsed: evidence truncated to 50 chars, criteria hidden
    expect(screen.queryByText('完成分层回测')).toBeNull()
    expect(screen.queryByText('证据全文')).toBeNull()

    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('完成分层回测')).toBeDefined()
    expect(screen.getByText('证据全文')).toBeDefined()
    // 展开后全文出现（preview 截断版 + 全文区）
    const hits = screen.getAllByText(/截面 IC = 0.045，这是一段很长的证据文本用于测试完整展示与折叠行为/)
    expect(hits.length).toBeGreaterThanOrEqual(1)
  })

  it('renders recap block when completed', () => {
    render(
      <GoalMessage
        message={goalMessage({ change_type: 'complete', goal_status: 'complete', recap: '总结内容' })}
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('总结内容')).toBeDefined()
  })
})
