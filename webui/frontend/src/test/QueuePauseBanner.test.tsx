import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueuePauseBanner } from '../components/chat/QueuePauseBanner'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'

describe('QueuePauseBanner', () => {
  beforeEach(() => {
    useChatStore.setState({
      queuePaused: new Map(),
      queueLengths: new Map(),
    })
    useSessionStore.setState({ currentSessionId: null })
  })

  it('renders the resume button', () => {
    render(<QueuePauseBanner />)
    expect(screen.getByTestId('queue-resume-btn')).toBeTruthy()
    expect(screen.getByText(/继续下一条/)).toBeTruthy()
  })

  it('shows pause label with pending count for the CURRENT session', () => {
    useChatStore.setState({
      queueLengths: new Map([['s1', 2], ['s2', 9]]),
    })
    useSessionStore.setState({ currentSessionId: 's1' })
    render(<QueuePauseBanner />)
    expect(screen.getByText(/剩余 2 条/)).toBeTruthy()
    // B12: must NOT show the max across sessions (9)
    expect(screen.queryByText(/剩余 9 条/)).toBeNull()
  })
})