// SSEStatus — renders the SSE/connection badge. Verifies that the
// 4 states (connected / connecting / disconnected / nostream) map to
// the right icon + label.

import { describe, it, expect, beforeEach, vi } from 'vitest'

let mockStatusValue = 'disconnected'
vi.mock('../stores/sse', () => ({
  useSSEStore: (selector: (s: { status: string }) => unknown) =>
    selector({ status: mockStatusValue }),
}))

vi.mock('../stores/system', () => ({
  useSystemStore: (selector: (s: { llm: { provider: string; model: string; configured: boolean }; fetchSystemInfo: () => Promise<void> }) => unknown) =>
    selector({
      llm: { provider: 'openai', model: 'gpt-4', configured: true },
      fetchSystemInfo: vi.fn().mockResolvedValue(undefined),
    }),
}))

vi.mock('lucide-react', () => {
  const Stub = ({ 'data-icon': id }: { 'data-icon'?: string }) => (
    <span data-icon={id} />
  )
  return {
    Wifi: (p: object) => <Stub {...p} data-icon="wifi" />,
    WifiOff: (p: object) => <Stub {...p} data-icon="wifioff" />,
    Loader2: (p: object) => <Stub {...p} data-icon="loader" />,
    Cpu: (p: object) => <Stub {...p} data-icon="cpu" />,
  }
})

import { render, screen } from '@testing-library/react'
import { SSEStatus } from '../components/common/SSEStatus'

beforeEach(() => {
  vi.clearAllMocks()
  mockStatusValue = 'disconnected'
})

describe('SSEStatus', () => {
  it('shows "已连接" + Wifi icon when connected', () => {
    mockStatusValue = 'connected'
    const { container } = render(<SSEStatus />)
    expect(screen.getByText('已连接')).toBeInTheDocument()
    expect(container.querySelector('[data-icon="wifi"]')).toBeTruthy()
  })

  it('shows "连接中" + Loader icon when connecting', () => {
    mockStatusValue = 'connecting'
    const { container } = render(<SSEStatus />)
    expect(screen.getByText('连接中')).toBeInTheDocument()
    expect(container.querySelector('[data-icon="loader"]')).toBeTruthy()
  })

  it('shows "已断开" + WifiOff icon when disconnected', () => {
    mockStatusValue = 'disconnected'
    const { container } = render(<SSEStatus />)
    expect(screen.getByText('已断开')).toBeInTheDocument()
    expect(container.querySelector('[data-icon="wifioff"]')).toBeTruthy()
  })

  it('renders the LLM summary line as "provider/model"', () => {
    mockStatusValue = 'connected'
    render(<SSEStatus />)
    expect(screen.getByText('openai/gpt-4')).toBeInTheDocument()
  })
})