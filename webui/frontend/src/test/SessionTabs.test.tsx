import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SessionTabs } from '../components/chat/SessionTabs'
import { useSessionStore } from '../stores/session'
import type { Session } from '../stores/session'

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

const makeSession = (id: string, overrides: Partial<Session> = {}): Session => ({
  id,
  user_id: 'u1',
  title: `Session ${id}`,
  created_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
  starred: false,
  tags: [],
  message_count: 0,
  archived: false,
  ...overrides,
})

describe('SessionTabs', () => {
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

  it('renders new-tab button when no sessions open', () => {
    render(<SessionTabs />)
    expect(screen.getByTitle(/新建会话/)).toBeInTheDocument()
  })

  it('renders one tab per open session', () => {
    useSessionStore.setState({
      sessions: [
        makeSession('a', { title: '会话 A' }),
        makeSession('b', { title: '会话 B' }),
      ],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    render(<SessionTabs />)
    expect(screen.getByText('会话 A')).toBeInTheDocument()
    expect(screen.getByText('会话 B')).toBeInTheDocument()
  })

  it('shows message_count badge', () => {
    useSessionStore.setState({
      sessions: [makeSession('a', { message_count: 5 })],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    render(<SessionTabs />)
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('shows star icon when starred', () => {
    useSessionStore.setState({
      sessions: [makeSession('a', { starred: true })],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    const { container } = render(<SessionTabs />)
    // lucide Star renders as svg with fill-amber-400
    const starSvg = container.querySelector('svg.fill-amber-400')
    expect(starSvg).toBeInTheDocument()
  })

  it('clicking tab calls switchSession', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    const switchSpy = vi.fn().mockResolvedValue(undefined)
    useSessionStore.setState({ switchSession: switchSpy })

    render(<SessionTabs />)
    fireEvent.click(screen.getByText('Session b'))
    expect(switchSpy).toHaveBeenCalledWith('b')
  })

  it('clicking + button creates new session', () => {
    const createSpy = vi.fn().mockResolvedValue(makeSession('new'))
    useSessionStore.setState({ createNewSession: createSpy })

    render(<SessionTabs />)
    fireEvent.click(screen.getByTitle(/新建会话/))
    expect(createSpy).toHaveBeenCalled()
  })

  it('right-click opens context menu', () => {
    useSessionStore.setState({
      sessions: [makeSession('a', { title: '我的会话' })],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    render(<SessionTabs />)
    const tab = screen.getByText('我的会话').closest('div')
    expect(tab).not.toBeNull()
    fireEvent.contextMenu(tab!)
    expect(screen.getByText('重命名')).toBeInTheDocument()
    expect(screen.getByText('编辑标签...')).toBeInTheDocument()
    expect(screen.getByText('删除会话')).toBeInTheDocument()
  })

  it('close button calls closeSession', () => {
    useSessionStore.setState({
      sessions: [makeSession('a')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    const closeSpy = vi.fn()
    useSessionStore.setState({ closeSession: closeSpy })

    const { container } = render(<SessionTabs />)
    const closeBtn = container.querySelector('button[title="关闭（保留历史）"]')
    expect(closeBtn).not.toBeNull()
    fireEvent.click(closeBtn!)
    expect(closeSpy).toHaveBeenCalledWith('a')
  })

  it('shows tag dots below tab when tags exist', () => {
    useSessionStore.setState({
      sessions: [makeSession('a', { tags: ['工作', '重要'] })],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    const { container } = render(<SessionTabs />)
    const dots = container.querySelectorAll('.h-1.w-1.rounded-full.bg-primary-500')
    expect(dots.length).toBeGreaterThan(0)
  })
})