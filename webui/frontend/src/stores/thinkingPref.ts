import { create } from 'zustand'

const KEY = 'sr-thinking-collapsed'

interface ThinkingPrefState {
  collapsed: boolean
  setCollapsed: (c: boolean) => void
  toggle: () => void
}

function loadInitial(): boolean {
  if (typeof window === 'undefined') return true
  const raw = localStorage.getItem(KEY)
  return raw === null ? true : raw !== 'false'
}

export const useThinkingPrefStore = create<ThinkingPrefState>()((set, get) => ({
  collapsed: loadInitial(),
  setCollapsed: (c) => {
    if (typeof window !== 'undefined') localStorage.setItem(KEY, String(c))
    set({ collapsed: c })
  },
  toggle: () => {
    const next = !get().collapsed
    if (typeof window !== 'undefined') localStorage.setItem(KEY, String(next))
    set({ collapsed: next })
  },
}))