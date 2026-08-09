import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, fireEvent } from '@testing-library/react'

// Hoisted mocks (vitest moves these above imports).
const mocks = vi.hoisted(() => ({
  togglePalette: vi.fn(),
  toggleRightPanel: vi.fn(),
  setSearchOpen: vi.fn(),
  createNewSession: vi.fn(),
  closeSession: vi.fn(),
  switchSession: vi.fn(),
  navigate: vi.fn(),
  toggleSidebar: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../stores/commandPalette', () => ({
  useCommandPaletteStore: (sel: (s: any) => any) =>
    sel({ toggle: mocks.togglePalette }),
}))

vi.mock('../stores/layout', () => ({
  useLayoutStore: (sel: (s: any) => any) => {
    const state = {
      toggleRightPanel: mocks.toggleRightPanel,
      toggleSidebar: mocks.toggleSidebar,
    }
    return sel(state)
  },
}))

vi.mock('../stores/session', () => ({
  useSessionStore: (sel: (s: any) => any) => {
    const state = {
      setSearchOpen: mocks.setSearchOpen,
      createNewSession: mocks.createNewSession,
      closeSession: mocks.closeSession,
      switchSession: mocks.switchSession,
      currentSessionId: 's-active',
      openSessionIds: ['s-active', 's-other'],
    }
    return sel(state)
  },
}))

// Import after mocks so the hook picks them up.
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'

function pressKey(key: string, init: Partial<KeyboardEventInit> = {}) {
  fireEvent.keyDown(document, {
    key,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    ...init,
  })
}

describe('useKeyboardShortcuts', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockClear())
  })

  // ── P43: Command palette on ⌘P / ⌘⇧P ──

  it('Cmd+P opens the command palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p', { metaKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Cmd+Shift+P also opens the command palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('P', { metaKey: true, shiftKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Ctrl+P also opens the command palette (Linux/Windows parity)', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p', { ctrlKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
  })

  it('Cmd+P inside an INPUT still triggers the palette', () => {
    // The shortcut is added to the skipIfInput allow-list alongside
    // ⌘K (search) and ⌘T (new tab), so typing in the composer must
    // not eat the shortcut.
    renderHook(() => useKeyboardShortcuts())
    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: 'p', metaKey: true })
    expect(mocks.togglePalette).toHaveBeenCalledTimes(1)
    document.body.removeChild(input)
  })

  it('plain "p" (no modifier) does NOT open the palette', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('p')
    expect(mocks.togglePalette).not.toHaveBeenCalled()
  })

  // ── pre-existing bindings still wired ──

  it('Cmd+K opens the search modal', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('k', { metaKey: true })
    expect(mocks.setSearchOpen).toHaveBeenCalledWith(true)
  })

  it('Cmd+B toggles the right panel', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('b', { metaKey: true })
    expect(mocks.toggleRightPanel).toHaveBeenCalledTimes(1)
  })

  it('Cmd+T creates a new session and navigates to /chat', () => {
    renderHook(() => useKeyboardShortcuts())
    pressKey('t', { metaKey: true })
    expect(mocks.createNewSession).toHaveBeenCalledWith('新会话')
    expect(mocks.navigate).toHaveBeenCalledWith('/chat')
  })
})