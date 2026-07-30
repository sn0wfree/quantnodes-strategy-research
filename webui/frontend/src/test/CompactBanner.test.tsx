import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { CompactBanner } from '../components/chat/CompactBanner'
import { useChatStore } from '../stores/chat'

beforeEach(() => {
  useChatStore.setState({ lastCompaction: null } as any)
})

describe('CompactBanner', () => {
  it('renders nothing when no compaction', () => {
    render(<CompactBanner />)
    expect(screen.queryByText('上下文已压缩')).toBeNull()
  })

  it('shows banner when lastCompaction is set', async () => {
    act(() => {
      useChatStore.setState({
        lastCompaction: { layer: 'llm_summarize(10->3)', timestamp: Date.now() },
      } as any)
    })
    render(<CompactBanner />)
    expect(screen.getByText('上下文已压缩: llm_summarize(10->3)')).toBeTruthy()
  })

  it('auto-dismisses after 5 seconds', async () => {
    vi.useFakeTimers()
    act(() => {
      useChatStore.setState({
        lastCompaction: { layer: 'microcompact(2)', timestamp: Date.now() },
      } as any)
    })
    render(<CompactBanner />)
    expect(screen.getByText('上下文已压缩: microcompact(2)')).toBeTruthy()

    act(() => {
      vi.advanceTimersByTime(5100)
    })

    expect(screen.queryByText('上下文已压缩: microcompact(2)')).toBeNull()
    vi.useRealTimers()
  })
})
