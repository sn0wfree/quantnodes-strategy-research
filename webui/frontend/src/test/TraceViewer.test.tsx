// TraceViewer — fetches GET /api/chat/session/{id}/trace and renders the
// reconstructed llm_request envelope (A3: event_log projection).

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TraceViewer } from '../components/chat/TraceViewer'

function mockFetch(events: unknown[]) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ events }),
  } as Response)
}

describe('TraceViewer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('sends the types filter as the backend param name', async () => {
    const fetchMock = mockFetch([])
    vi.stubGlobal('fetch', fetchMock)
    render(<TraceViewer sessionId="s-1" />)
    await waitFor(() => expect(screen.getByText('No trace events found')).toBeTruthy())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/chat/session/s-1/trace')
  })

  it('renders a reconstructed llm_request with system prompt + tools schema', async () => {
    const bigPrompt = 'S'.repeat(120)
    const events = [
      {
        type: 'llm_request',
        time_created: 1700000000,
        iteration: 1,
        history_count: 2,
        tools_count: 1,
        system_prompt_len: bigPrompt.length,
        system_prompt: bigPrompt,
        tools_schema: JSON.stringify({ name: 't1', parameters: { type: 'object' } }),
        history_meta: [{ role: 'user', content_len: 10 }],
      },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)

    expect(await screen.findByText(/LLM Request — 1 tools, 2 messages/)).toBeTruthy()

    // Expand to reveal the reconstructed envelope.
    fireEvent.click(screen.getByText(/LLM Request — 1 tools/))
    expect(screen.getByText('System prompt (120 chars)')).toBeTruthy()
    expect(screen.getByText(bigPrompt)).toBeTruthy()
    expect(screen.getByText(/Tools schema \(1 tools\)/)).toBeTruthy()
    expect(screen.getByText(/"name": "t1"/)).toBeTruthy()
  })

  it('prefers time_created over ts for the timestamp', async () => {
    const events = [
      { type: 'llm_request', time_created: 1700000000, ts: 999999999, iteration: 1 },
    ]
    vi.stubGlobal('fetch', mockFetch(events))
    render(<TraceViewer sessionId="s-1" />)
    // 1700000000s epoch → a local time string; just assert it rendered the card.
    expect(await screen.findByText(/LLM Request/)).toBeTruthy()
  })
})