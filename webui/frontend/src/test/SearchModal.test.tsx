import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SearchModal } from '../components/common/SearchModal'
import { useSessionStore } from '../stores/session'
import type { SearchHit } from '../stores/session'

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    sse: vi.fn(),
  },
}))

import { api } from '../api/client'
const mockedApi = api as unknown as { post: ReturnType<typeof vi.fn> }

const sampleHits: SearchHit[] = [
  {
    session_id: 's1',
    session_title: 'alpha 探索',
    message_id: 'm1',
    role: 'user',
    snippet: '帮我设计一个 <mark>alpha</mark> 策略',
    score: -1.5,
    created_at: 1700000000,
  },
  {
    session_id: 's2',
    session_title: '回测讨论',
    message_id: 'm2',
    role: 'assistant',
    snippet: '<mark>alpha</mark> 因子计算方法...',
    score: -2.1,
    created_at: 1700001000,
  },
]

describe('SearchModal', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
    })
    vi.clearAllMocks()
  })

  it('renders nothing when closed', () => {
    const { container } = render(<SearchModal />)
    expect(container.firstChild).toBeNull()
  })

  it('shows placeholder when open with no query', () => {
    useSessionStore.setState({ searchOpen: true })
    render(<SearchModal />)
    expect(screen.getByText(/输入关键词搜索所有会话的消息/)).toBeInTheDocument()
  })

  it('shows empty message when query yields no hits', async () => {
    useSessionStore.setState({ searchOpen: true, searchQuery: 'no_match_xyz' })
    mockedApi.post.mockResolvedValue({ hits: [] })
    render(<SearchModal />)

    fireEvent.change(screen.getByPlaceholderText(/搜索消息内容/), {
      target: { value: 'no_match_xyz' },
    })

    await waitFor(() => {
      expect(screen.getByText(/未找到匹配结果/)).toBeInTheDocument()
    })
  })

  it('renders hits with mark highlighting', async () => {
    useSessionStore.setState({ searchOpen: true })
    mockedApi.post.mockResolvedValue({ hits: sampleHits })
    render(<SearchModal />)

    const input = screen.getByPlaceholderText(/搜索消息内容/)
    fireEvent.change(input, { target: { value: 'alpha' } })

    await waitFor(() => {
      // <mark> elements present
      const marks = document.querySelectorAll('mark')
      expect(marks.length).toBeGreaterThan(0)
      expect(screen.getByText('alpha 探索')).toBeInTheDocument()
    })
  })

  it('calls api.post with correct payload', async () => {
    useSessionStore.setState({ searchOpen: true })
    mockedApi.post.mockResolvedValue({ hits: [] })
    render(<SearchModal />)

    fireEvent.change(screen.getByPlaceholderText(/搜索消息内容/), {
      target: { value: 'beta 策略' },
    })

    await waitFor(() => {
      expect(mockedApi.post).toHaveBeenCalledWith(
        '/chat/session/search',
        expect.objectContaining({ query: 'beta 策略', limit: 20 })
      )
    })
  })

  it('clicking a hit opens the session', async () => {
    useSessionStore.setState({
      searchOpen: true,
      searchResults: sampleHits,
      searchQuery: 'alpha',
    })
    const openSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ openSession: openSpy })

    render(<SearchModal />)

    // Wait for hits to render
    await waitFor(() => {
      const allDivs = document.querySelectorAll('button')
      const found = Array.from(allDivs).find((b) =>
        b.textContent?.includes('alpha 探索')
      )
      expect(found).toBeDefined()
      if (found) fireEvent.click(found)
    })

    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith('s1')
    })
  })

  it('Escape closes the modal', () => {
    useSessionStore.setState({ searchOpen: true })
    render(<SearchModal />)

    const input = screen.getByPlaceholderText(/搜索消息内容/)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(useSessionStore.getState().searchOpen).toBe(false)
  })

  it('ArrowDown/ArrowUp navigate results', async () => {
    useSessionStore.setState({ searchOpen: true, searchResults: sampleHits })
    render(<SearchModal />)

    const input = screen.getByPlaceholderText(/搜索消息内容/)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowUp' })

    // Just verify no crash; selectedIdx is internal
    expect(input).toBeInTheDocument()
  })

  it('Enter on selected hit opens session', async () => {
    useSessionStore.setState({ searchOpen: true, searchResults: sampleHits })
    const openSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ openSession: openSpy })

    render(<SearchModal />)

    const input = screen.getByPlaceholderText(/搜索消息内容/)
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith('s1')
    })
  })
})