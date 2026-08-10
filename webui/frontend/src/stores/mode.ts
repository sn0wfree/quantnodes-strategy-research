import { create } from 'zustand'

type AgentMode = 'plan' | 'build'

interface ModeState {
  /** Current mode for the active session. */
  mode: AgentMode
  /** Set mode for the current session. Persists to localStorage. */
  setMode: (m: AgentMode) => void
  /** Toggle between plan and build. */
  toggleMode: () => void
  /** Load persisted mode for a session. */
  loadSessionMode: (sessionId: string) => void
}

function loadPersisted(sessionId: string): AgentMode {
  if (typeof window === 'undefined') return 'build'
  const raw = localStorage.getItem(`sr-mode-${sessionId}`)
  return raw === 'plan' ? 'plan' : 'build'
}

function persist(sessionId: string, mode: AgentMode) {
  if (typeof window !== 'undefined' && sessionId) {
    localStorage.setItem(`sr-mode-${sessionId}`, mode)
  }
}

export const useModeStore = create<ModeState>()((set, get) => ({
  mode: 'build',
  setMode: (m) => {
    set({ mode: m })
    // Session ID is not stored here; caller must call loadSessionMode first
    // or use the store within a session context
  },
  toggleMode: () => {
    const next = get().mode === 'plan' ? 'build' : 'plan'
    set({ mode: next })
  },
  loadSessionMode: (sessionId) => {
    const mode = loadPersisted(sessionId)
    set({ mode })
  },
}))

// Subscribe to mode changes and persist
if (typeof window !== 'undefined') {
  let lastSessionId: string | null = null
  useModeStore.subscribe((state) => {
    // We need the session ID to persist; get it from session store
    // This is a workaround since mode store doesn't own session ID
    if (lastSessionId) {
      persist(lastSessionId, state.mode)
    }
  })
  // Expose setter for session ID (called by Composer on mount)
  ;(window as any).__srModeSession = (id: string) => { lastSessionId = id }
}
