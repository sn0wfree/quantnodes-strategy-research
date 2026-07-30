import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueuePauseBanner } from '../components/chat/QueuePauseBanner'
import { useChatStore } from '../stores/chat'

describe('QueuePauseBanner', () => {
  beforeEach(() => {
    useChatStore.setState({
      queuePaused: new Map(),
      queueLengths: new Map(),
    })
  })

  it('renders the resume button', () => {
    render(<QueuePauseBanner />)
    expect(screen.getByTestId('queue-resume-btn')).toBeTruthy()
    expect(screen.getByText(/继续下一条/)).toBeTruthy()
  })

  it('shows pause label with pending count when queue lengths are tracked', () => {
    useChatStore.setState({
      queueLengths: new Map([['s1', 2]]),
    })
    render(<QueuePauseBanner />)
    expect(screen.getByText(/剩余 2 条/)).toBeTruthy()
  })
})