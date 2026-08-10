import { create } from 'zustand'

const KEY_COLLAPSED = 'sr-thinking-collapsed'
const KEY_ENABLED = 'sr-thinking-enabled'
const KEY_THINKING_MODE = 'sr-thinking-mode'

type ThinkingMode = 'off' | 'on' | 'auto'

interface ThinkingPrefState {
  /** Per-message default folded state for non-streaming thinking. */
  collapsed: boolean
  setCollapsed: (c: boolean) => void
  toggle: () => void
  /**
   * Global kill-switch: when false, ThinkingBlock renders nothing
   * regardless of streaming / collapsed state. Default true.
   * Persisted under sr-thinking-enabled.
   */
  enabled: boolean
  setEnabled: (e: boolean) => void
  toggleEnabled: () => void
  /**
   * Thinking mode: controls how thinking/reasoning is used.
   * "off" = no thinking blocks, "on" = always think, "auto" = provider decides.
   * Persisted under sr-thinking-mode.
   */
  thinkingMode: ThinkingMode
  setThinkingMode: (m: ThinkingMode) => void
}

function loadInitialCollapsed(): boolean {
  if (typeof window === 'undefined') return true
  const raw = localStorage.getItem(KEY_COLLAPSED)
  return raw === null ? true : raw !== 'false'
}

function loadInitialEnabled(): boolean {
  if (typeof window === 'undefined') return true
  const raw = localStorage.getItem(KEY_ENABLED)
  return raw === null ? true : raw !== 'false'
}

function loadInitialThinkingMode(): ThinkingMode {
  if (typeof window === 'undefined') return 'auto'
  const raw = localStorage.getItem(KEY_THINKING_MODE)
  if (raw === 'off' || raw === 'on' || raw === 'auto') return raw
  return 'auto'
}

export const useThinkingPrefStore = create<ThinkingPrefState>()((set, get) => ({
  collapsed: loadInitialCollapsed(),
  setCollapsed: (c) => {
    if (typeof window !== 'undefined') localStorage.setItem(KEY_COLLAPSED, String(c))
    set({ collapsed: c })
  },
  toggle: () => {
    const next = !get().collapsed
    if (typeof window !== 'undefined') localStorage.setItem(KEY_COLLAPSED, String(next))
    set({ collapsed: next })
  },
  enabled: loadInitialEnabled(),
  setEnabled: (e) => {
    if (typeof window !== 'undefined') localStorage.setItem(KEY_ENABLED, String(e))
    set({ enabled: e })
  },
  toggleEnabled: () => {
    const next = !get().enabled
    if (typeof window !== 'undefined') localStorage.setItem(KEY_ENABLED, String(next))
    set({ enabled: next })
  },
  thinkingMode: loadInitialThinkingMode(),
  setThinkingMode: (m) => {
    if (typeof window !== 'undefined') localStorage.setItem(KEY_THINKING_MODE, m)
    set({ thinkingMode: m })
  },
}))