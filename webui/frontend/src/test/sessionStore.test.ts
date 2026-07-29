import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useSessionStore } from '../stores/session'
import type { Session, SearchHit } from '../stores/session'

const makeSession = (
  id: string,
  overrides: Partial<Session> = {}
): Session => ({
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
const mockedApi = api as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

describe('useSessionStore — basic', () => {
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

  it('starts empty', () => {
    expect(useSessionStore.getState().sessions).toEqual([])
    expect(useSessionStore.getState().currentSessionId).toBeNull()
    expect(useSessionStore.getState().openSessionIds).toEqual([])
  })

  it('setSessions replaces all', () => {
    useSessionStore.getState().setSessions([makeSession('a'), makeSession('b')])
    expect(useSessionStore.getState().sessions.length).toBe(2)
  })

  it('addSession prepends and dedupes', () => {
    useSessionStore.getState().addSession(makeSession('a'))
    useSessionStore.getState().addSession(makeSession('b'))
    useSessionStore.getState().addSession(makeSession('a')) // dedupe
    const sessions = useSessionStore.getState().sessions
    expect(sessions.length).toBe(2)
    expect(sessions[0].id).toBe('a')
    expect(sessions[1].id).toBe('b')
  })

  it('setCurrentSession updates id', () => {
    useSessionStore.getState().setCurrentSession('sess-1')
    expect(useSessionStore.getState().currentSessionId).toBe('sess-1')
  })

  it('removeSession removes + clears open + clears current if matching', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    useSessionStore.getState().removeSession('a')
    const state = useSessionStore.getState()
    expect(state.sessions.length).toBe(1)
    expect(state.openSessionIds).toEqual(['b'])
    expect(state.currentSessionId).toBeNull()
  })
})

describe('useSessionStore — async actions', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
    })
    vi.clearAllMocks()
  })

  it('loadSessions fetches and stores', async () => {
    mockedApi.get.mockResolvedValueOnce({
      sessions: [makeSession('a'), makeSession('b')],
    })
    await useSessionStore.getState().loadSessions()
    expect(useSessionStore.getState().sessions.length).toBe(2)
  })

  it('createNewSession adds to open + current', async () => {
    mockedApi.post.mockResolvedValueOnce(makeSession('new', { title: '我的会话' }))
    const sess = await useSessionStore.getState().createNewSession('我的会话')
    const state = useSessionStore.getState()
    expect(sess.id).toBe('new')
    expect(state.openSessionIds).toContain('new')
    expect(state.currentSessionId).toBe('new')
  })

  it('switchSession updates current + openSessionIds', async () => {
    // Setup: 2 sessions already exist
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a', 'b'],
      currentSessionId: 'a',
    })
    // loadMessages calls api.get — return empty messages
    mockedApi.get.mockResolvedValue({ messages: [] })
    await useSessionStore.getState().switchSession('b')
    expect(useSessionStore.getState().currentSessionId).toBe('b')
    expect(useSessionStore.getState().openSessionIds).toEqual(['a', 'b'])
  })

  it('switchSession adds id to openSessionIds if not present', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    mockedApi.get.mockResolvedValue({ messages: [] })
    await useSessionStore.getState().switchSession('b')
    expect(useSessionStore.getState().openSessionIds).toEqual(['a', 'b'])
  })

  it('switchSession to same id is no-op', async () => {
    useSessionStore.setState({
      sessions: [makeSession('a')],
      openSessionIds: ['a'],
      currentSessionId: 'a',
    })
    // Count currentSessionId state changes — should be 0
    const initial = useSessionStore.getState().currentSessionId
    await useSessionStore.getState().switchSession('a')
    expect(useSessionStore.getState().currentSessionId).toBe(initial)
  })

  it('updateSessionMeta optimistic + API call', async () => {
    useSessionStore.setState({ sessions: [makeSession('a', { starred: false })] })
    mockedApi.patch.mockResolvedValueOnce(makeSession('a', { starred: true }))
    await useSessionStore.getState().updateSessionMeta('a', { starred: true })
    expect(useSessionStore.getState().sessions[0].starred).toBe(true)
    expect(mockedApi.patch).toHaveBeenCalledWith('/chat/session/a', { starred: true })
  })

  it('closeSession picks neighbor', () => {
    useSessionStore.setState({
      openSessionIds: ['a', 'b', 'c'],
      currentSessionId: 'b',
      sessions: [makeSession('a'), makeSession('b'), makeSession('c')],
    })
    useSessionStore.getState().closeSession('b')
    const state = useSessionStore.getState()
    // Neighbor after b is c (index 2 in original list, now [a, c])
    expect(state.openSessionIds).toEqual(['a', 'c'])
    // switchSession runs async; we can't wait easily, but openSessionIds is sync
  })

  it('runSearch hits store', async () => {
    const hits: SearchHit[] = [{
      session_id: 'a', session_title: '会话 A', message_id: 'm1',
      role: 'user', snippet: 'foo', score: -1, created_at: 0,
    }]
    mockedApi.post.mockResolvedValueOnce({ hits })
    await useSessionStore.getState().runSearch('foo')
    expect(useSessionStore.getState().searchResults).toEqual(hits)
  })

  it('runSearch empty query clears without calling API', async () => {
    useSessionStore.setState({ searchResults: [{ session_id: 'x', session_title: '', message_id: '', role: 'user', snippet: '', score: 0, created_at: 0 }] })
    await useSessionStore.getState().runSearch('   ')
    expect(useSessionStore.getState().searchResults).toEqual([])
    expect(mockedApi.post).not.toHaveBeenCalled()
  })
})