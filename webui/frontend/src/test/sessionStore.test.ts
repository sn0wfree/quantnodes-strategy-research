import { describe, it, expect, beforeEach } from 'vitest'
import { useSessionStore } from '../stores/session'
import type { Session } from '../stores/session'

const makeSession = (id: string, title = `Session ${id}`): Session => ({
  id,
  title,
  created_at: Date.now() / 1000,
  updated_at: Date.now() / 1000,
})

describe('useSessionStore', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
    })
  })

  it('starts empty', () => {
    expect(useSessionStore.getState().sessions).toEqual([])
    expect(useSessionStore.getState().currentSessionId).toBeNull()
  })

  it('setSessions replaces all', () => {
    useSessionStore.getState().setSessions([makeSession('a'), makeSession('b')])
    expect(useSessionStore.getState().sessions.length).toBe(2)
  })

  it('addSession prepends to list', () => {
    useSessionStore.getState().addSession(makeSession('a'))
    useSessionStore.getState().addSession(makeSession('b'))
    const sessions = useSessionStore.getState().sessions
    expect(sessions[0].id).toBe('b')
    expect(sessions[1].id).toBe('a')
  })

  it('setCurrentSession updates id', () => {
    useSessionStore.getState().setCurrentSession('sess-1')
    expect(useSessionStore.getState().currentSessionId).toBe('sess-1')

    useSessionStore.getState().setCurrentSession(null)
    expect(useSessionStore.getState().currentSessionId).toBeNull()
  })

  it('removeSession removes from list and clears current if matching', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      currentSessionId: 'a',
    })
    useSessionStore.getState().removeSession('a')
    const state = useSessionStore.getState()
    expect(state.sessions.length).toBe(1)
    expect(state.sessions[0].id).toBe('b')
    expect(state.currentSessionId).toBeNull()
  })

  it('removeSession keeps currentSessionId if non-matching', () => {
    useSessionStore.setState({
      sessions: [makeSession('a'), makeSession('b')],
      currentSessionId: 'a',
    })
    useSessionStore.getState().removeSession('b')
    expect(useSessionStore.getState().currentSessionId).toBe('a')
  })
})