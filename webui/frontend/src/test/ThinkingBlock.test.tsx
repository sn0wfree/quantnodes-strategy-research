import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ThinkingBlock } from '../components/chat/ThinkingBlock'
import { useThinkingPrefStore } from '../stores/thinkingPref'

// Mock navigator.clipboard
const writeText = vi.fn().mockResolvedValue(undefined)
Object.assign(navigator, { clipboard: { writeText } })

describe('ThinkingBlock', () => {
  beforeEach(() => {
    // Reset the store to default values between tests so the
    // enabled/disabled toggle has a deterministic starting point.
    useThinkingPrefStore.setState({
      collapsed: true,
      enabled: true,
    })
    writeText.mockClear()
  })

  it('renders single-line header when collapsed', () => {
    render(<ThinkingBlock text="reasoning content here" collapsed />)
    expect(screen.getByText(/Thought/)).toBeTruthy()
    // Content not visible until expanded
    expect(screen.queryByText('reasoning content here')).toBeNull()
  })

  it('shows content when expanded', () => {
    render(<ThinkingBlock text="reasoning content here" collapsed={false} />)
    expect(screen.getByText('reasoning content here')).toBeTruthy()
  })

  it('shows "Thinking…" while streaming without startTime', () => {
    render(<ThinkingBlock text="thinking" streaming />)
    // Either "Thinking…" or "Thinking for Xs" — both are valid.
    const el = screen.getByText(/Thinking/)
    expect(el).toBeTruthy()
  })

  it('shows "Thought for Xs" when both startTime and endTime provided', () => {
    const start = 1000
    const end = 3500
    render(<ThinkingBlock text="t" startTime={start} endTime={end} />)
    expect(screen.getByText(/Thought for 2\.5s/)).toBeTruthy()
  })

  it('does not render when text is empty', () => {
    const { container } = render(<ThinkingBlock text="" />)
    expect(container.firstChild).toBeNull()
  })

  it('copies text to clipboard when copy icon clicked', async () => {
    writeText.mockClear()
    render(<ThinkingBlock text="hello to copy" collapsed={false} />)
    const copyBtn = screen.getByTitle('复制')
    fireEvent.click(copyBtn)
    expect(writeText).toHaveBeenCalledWith('hello to copy')
  })

  // ── P21: global kill-switch ──

  it('renders nothing when global enabled=false', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container } = render(
      <ThinkingBlock text="should not render" collapsed={false} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('global enabled=false suppresses streaming blocks too', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container } = render(<ThinkingBlock text="live" streaming />)
    expect(container.firstChild).toBeNull()
  })

  it('toggling enabled restores rendering', () => {
    useThinkingPrefStore.setState({ enabled: false })
    const { container, rerender } = render(
      <ThinkingBlock text="should appear" collapsed={false} />
    )
    expect(container.firstChild).toBeNull()
    useThinkingPrefStore.setState({ enabled: true })
    rerender(<ThinkingBlock text="should appear" collapsed={false} />)
    expect(screen.getByText('should appear')).toBeTruthy()
  })
})