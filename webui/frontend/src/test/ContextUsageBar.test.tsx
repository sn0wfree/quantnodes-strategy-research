import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ContextUsageBar } from '../components/chat/ContextUsageBar'
import { useChatStore } from '../stores/chat'
import { useSessionStore } from '../stores/session'
import { useSystemStore } from '../stores/system'

const setup = (opts: {
  sessionId?: string | null
  tokensUsed?: number
  context?: number
  source?: 'bundled' | 'cached' | 'fetched' | 'fallback'
}) => {
  useSessionStore.setState({
    currentSessionId: opts.sessionId ?? 's1',
  } as any)
  useChatStore.setState({
    tokensUsed: new Map(
      opts.sessionId ? [[opts.sessionId, opts.tokensUsed ?? 0]] : []
    ),
  })
  useSystemStore.setState({
    modelInfo: opts.context
      ? {
          provider: 'minimax',
          model: 'minimax-M3',
          models_dev_id: 'minimax-cn-coding-plan',
          context_tokens: opts.context,
          max_output_tokens: 128000,
          supports_vision: true,
          supports_audio: false,
          supports_pdf: false,
          supports_tools: true,
          supports_reasoning: true,
          supports_structured_output: false,
          cost_input: null,
          cost_output: null,
          cost_cache_read: null,
          cost_cache_write: null,
          description: '',
          release_date: null,
          source: opts.source ?? 'fetched',
          fetched_at: null,
        }
      : null,
  })
}

describe('ContextUsageBar', () => {
  beforeEach(() => {
    useChatStore.setState({
      tokensUsed: new Map(),
    })
    useSessionStore.setState({ currentSessionId: null } as any)
    useSystemStore.setState({ modelInfo: null })
  })

  it('renders nothing when modelInfo is missing', () => {
    setup({ sessionId: 's1', tokensUsed: 100, context: 0 })
    const { container } = render(<ContextUsageBar />)
    expect(container.firstChild).toBeNull()
  })

  it('renders token usage text with correct format', () => {
    setup({ sessionId: 's1', tokensUsed: 8200, context: 128000, source: 'fetched' })
    render(<ContextUsageBar />)
    const text = screen.getByTestId('context-usage-text')
    expect(text.textContent).toMatch(/8\.2K \/ 128\.0K/)
    expect(text.textContent).toMatch(/6\.4%/)
  })

  it('formats large numbers with M suffix', () => {
    setup({ sessionId: 's1', tokensUsed: 2_500_000, context: 1_000_000, source: 'fetched' })
    render(<ContextUsageBar />)
    const text = screen.getByTestId('context-usage-text')
    expect(text.textContent).toMatch(/2\.5M \/ 1\.0M/)
  })

  it('applies green color tier for low usage', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-emerald-500')
  })

  it('applies amber color tier for 50-80% usage', () => {
    setup({ sessionId: 's1', tokensUsed: 60000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-amber-500')
  })

  it('applies red color tier for >=80% usage', () => {
    setup({ sessionId: 's1', tokensUsed: 85000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    const bar = screen.getByTestId('context-progress-bar')
    expect(bar.className).toContain('bg-red-500')
  })

  it('shows stale hint when source is fallback', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fallback' })
    render(<ContextUsageBar />)
    expect(screen.getByTestId('context-stale-hint')).toBeTruthy()
  })

  it('shows stale hint when source is bundled', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'bundled' })
    render(<ContextUsageBar />)
    expect(screen.getByTestId('context-stale-hint')).toBeTruthy()
  })

  it('no stale hint when source is fetched', () => {
    setup({ sessionId: 's1', tokensUsed: 1000, context: 100000, source: 'fetched' })
    render(<ContextUsageBar />)
    expect(screen.queryByTestId('context-stale-hint')).toBeNull()
  })

  it('reads tokensUsed from the current sessionId', () => {
    useChatStore.setState({
      tokensUsed: new Map([
        ['s1', 500],
        ['s2', 99999],
      ]),
    })
    useSessionStore.getState().setCurrentSession('s2')
    useSystemStore.setState({
      modelInfo: {
        provider: 'minimax',
        model: 'minimax-M3',
        models_dev_id: 'minimax-cn-coding-plan',
        context_tokens: 100000,
        max_output_tokens: 128000,
        supports_vision: true,
        supports_audio: false,
        supports_pdf: false,
        supports_tools: true,
        supports_reasoning: true,
        supports_structured_output: false,
        cost_input: null,
        cost_output: null,
        cost_cache_read: null,
        cost_cache_write: null,
        description: '',
        release_date: null,
        source: 'fetched',
        fetched_at: null,
      },
    })
    render(<ContextUsageBar />)
    // 99999 rounds to 100.0K via 99999/1000 = 99.999 → 100.0
    expect(screen.getByTestId('context-usage-text').textContent).toMatch(/100\.0K/)
  })
})
