// auth store unit tests — setAuth / logout / persist rehydrate.

import { describe, it, expect, beforeEach } from 'vitest'
import { useAuthStore } from '../stores/auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, user: null })
    localStorage.clear()
  })

  it('starts unauthenticated', () => {
    const s = useAuthStore.getState()
    expect(s.token).toBeNull()
    expect(s.user).toBeNull()
  })

  it('stores token and user via setAuth', () => {
    useAuthStore.getState().setAuth('tok-abc', {
      id: 'u1', username: 'tester', display_name: 'Tester',
    })
    const s = useAuthStore.getState()
    expect(s.token).toBe('tok-abc')
    expect(s.user?.username).toBe('tester')
  })

  it('clears state on logout', () => {
    useAuthStore.getState().setAuth('tok', {
      id: 'u1', username: 'tester', display_name: 'Tester',
    })
    useAuthStore.getState().logout()
    const s = useAuthStore.getState()
    expect(s.token).toBeNull()
    expect(s.user).toBeNull()
  })

  it('persists token to localStorage under the configured key', () => {
    useAuthStore.getState().setAuth('tok-persisted', {
      id: 'u2', username: 'persisted', display_name: 'Persisted',
    })
    const raw = localStorage.getItem('sr-auth')
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw!)
    expect(parsed.state.token).toBe('tok-persisted')
    expect(parsed.state.user.username).toBe('persisted')
  })
})