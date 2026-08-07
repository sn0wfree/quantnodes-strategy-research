import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProgressTab } from '../components/goal/ProgressTab'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import type { Message, MessagePart } from '../stores/chat'

const mockCard = vi.fn()
vi.mock('../components/performance/EquityCurveCard', () => ({
  EquityCurveCard: (p: { curve: unknown }) => {
    mockCard(p)
    return <div data-testid="equity-card" />
  },
}))

function makeMsg(id: string, parts: MessagePart[]): Message {
  return { id, session_id: 's1', role: 'assistant', parts, created_at: 1 }
}

const GOAL = {
  id: 'g1',
  title: '研究动量因子',
  description: '',
  status: 'active' as const,
  criteria: [],
  timeline: [],
}

describe('ProgressTab', () => {
  beforeEach(() => {
    mockCard.mockClear()
    useChatStore.setState({ messages: new Map() })
    useSessionStore.setState({ currentSessionId: 's1' })
  })

  it('passes null curve when no backtest equity data exists', () => {
    render(<ProgressTab goal={GOAL} />)
    expect(mockCard).toHaveBeenCalledWith({ curve: null })
    expect(screen.getByText('研究动量因子')).toBeTruthy()
  })

  it('passes the decoded curve from the session messages', () => {
    useChatStore.setState({
      messages: new Map([
        ['m1', makeMsg('m1', [{
          type: 'chart', chart_type: 'line', title: '净值曲线',
          data: [{ date: 'd1', nav: 1.0 }, { date: 'd2', nav: 1.1 }],
        }])],
      ]),
    })
    render(<ProgressTab goal={GOAL} />)
    const curve = mockCard.mock.calls[0][0].curve
    expect(curve.title).toBe('净值曲线')
    expect(curve.points).toHaveLength(2)
  })
})