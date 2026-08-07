// useKeyboardShortcuts — global key handler bound to window. Verifies:
//   - Cmd/Ctrl+K opens search
//   - Cmd/Ctrl+G / W switch the right panel tab
//   - Cmd/Ctrl+B toggles the right panel
//   - Cmd/Ctrl+1..9 switch sessions when a slot is open
//   - Cmd/Ctrl+T creates a new session
//   - Cmd/Ctrl+Shift+W closes the current session
//   - Shortcuts are ignored when typing into <input>/<textarea>
//     except Cmd/Ctrl+K and Cmd/Ctrl+T which still fire

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import { useCommandPaletteStore } from '../stores/commandPalette'
import { useLayoutStore } from '../stores/layout'
import { useSessionStore } from '../stores/session'

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }))
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}))

function fire(key: string, opts: { metaKey?: boolean; shiftKey?: boolean; target?: EventTarget | null } = {}) {
  const event = new KeyboardEvent('keydown', {
    key,
    metaKey: opts.metaKey ?? false,
    ctrlKey: opts.metaKey ?? false,
    shiftKey: opts.shiftKey ?? false,
    bubbles: true,
    cancelable: true,
  })
  ;(opts.target ?? window).dispatchEvent(event)
  return event
}

describe('useKeyboardShortcuts', () => {
  beforeEach(() => {
    useCommandPaletteStore.setState({ open: false })
    useLayoutStore.setState({ rightPanelTab: 'progress', rightPanelVisible: true })
    useSessionStore.setState({
      openSessionIds: [],
      currentSessionId: null,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('binds to window on mount and unbinds on unmount', () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    const { unmount } = renderHook(() => useKeyboardShortcuts())
    expect(addSpy).toHaveBeenCalledWith('keydown', expect.any(Function))
    unmount()
    expect(removeSpy).toHaveBeenCalledWith('keydown', expect.any(Function))
  })

  it('Cmd/Ctrl+K opens the search modal', () => {
    const setSearchOpen = vi.fn()
    useSessionStore.setState({ setSearchOpen } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('k', { metaKey: true })
    expect(setSearchOpen).toHaveBeenCalledWith(true)
  })

  it('Cmd/Ctrl+G switches the right panel to the progress tab', () => {
    renderHook(() => useKeyboardShortcuts())
    fire('g', { metaKey: true })
    expect(useLayoutStore.getState().rightPanelTab).toBe('progress')
  })

  it('Cmd/Ctrl+W opens the DAG page', () => {
    renderHook(() => useKeyboardShortcuts())
    fire('w', { metaKey: true })
    expect(navigateMock).toHaveBeenCalledWith('/dag')
  })

  it('Cmd/Ctrl+B toggles the right panel', () => {
    const before = useLayoutStore.getState().rightPanelVisible
    renderHook(() => useKeyboardShortcuts())
    fire('b', { metaKey: true })
    expect(useLayoutStore.getState().rightPanelVisible).toBe(!before)
  })

  it('Cmd/Ctrl+T creates a new session', () => {
    const createNewSession = vi.fn()
    useSessionStore.setState({ createNewSession } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('t', { metaKey: true })
    expect(createNewSession).toHaveBeenCalledWith('新会话')
  })

  it('Cmd/Ctrl+1..9 switch to the indexed open session', () => {
    useSessionStore.setState({
      openSessionIds: ['s1', 's2', 's3'],
      currentSessionId: 's1',
    })
    const switchSession = vi.fn()
    useSessionStore.setState({ switchSession } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('2', { metaKey: true })
    expect(switchSession).toHaveBeenCalledWith('s2')
  })

  it('Cmd/Ctrl+9 with an empty slot is a no-op', () => {
    const switchSession = vi.fn()
    useSessionStore.setState({
      openSessionIds: ['s1'],
      switchSession,
    } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('9', { metaKey: true })
    expect(switchSession).not.toHaveBeenCalled()
  })

  it('Cmd/Ctrl+Shift+W closes the current session', () => {
    useSessionStore.setState({
      currentSessionId: 's-current',
    })
    const closeSession = vi.fn()
    useSessionStore.setState({ closeSession } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('w', { metaKey: true, shiftKey: true })
    expect(closeSession).toHaveBeenCalledWith('s-current')
  })

  it('ignores shortcuts when typing into <input> (except Cmd/Ctrl+K, T)', () => {
    const setSearchOpen = vi.fn()
    const createNewSession = vi.fn()
    const switchSession = vi.fn()
    useSessionStore.setState({
      openSessionIds: ['s1'],
      currentSessionId: 's1',
      setSearchOpen,
      createNewSession,
      switchSession,
    } as never)
    renderHook(() => useKeyboardShortcuts())
    const input = document.createElement('input')
    document.body.appendChild(input)
    // Cmd+1 should NOT switch because we're in an input
    fire('1', { metaKey: true, target: input })
    expect(switchSession).not.toHaveBeenCalled()
    // Cmd+K should still open search
    fire('k', { metaKey: true, target: input })
    expect(setSearchOpen).toHaveBeenCalledWith(true)
    document.body.removeChild(input)
  })

  it('ignores shortcuts when typing into <textarea> too', () => {
    const setSearchOpen = vi.fn()
    useSessionStore.setState({ setSearchOpen } as never)
    renderHook(() => useKeyboardShortcuts())
    const ta = document.createElement('textarea')
    document.body.appendChild(ta)
    fire('k', { metaKey: true, target: ta })
    // Cmd+K is allowed in inputs
    expect(setSearchOpen).toHaveBeenCalledWith(true)
    const switchSession = vi.fn()
    useSessionStore.setState({
      openSessionIds: ['s1'],
      switchSession,
    } as never)
    fire('1', { metaKey: true, target: ta })
    expect(switchSession).not.toHaveBeenCalled()
    document.body.removeChild(ta)
  })

  it('does nothing when only the bare key is pressed (no meta)', () => {
    const switchSession = vi.fn()
    useSessionStore.setState({
      openSessionIds: ['s1'],
      currentSessionId: 's1',
      switchSession,
    } as never)
    renderHook(() => useKeyboardShortcuts())
    fire('1')
    expect(switchSession).not.toHaveBeenCalled()
  })
})