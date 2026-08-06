import { create } from 'zustand'
import { api } from '../api/client'
import type { ChatPersona } from '../api/client'

const PERSONA_KEY = 'sr-persona'

interface PersonaState {
  personas: ChatPersona[]
  loaded: boolean
  /** Per-session selected persona id (falls back to 'chat'). */
  sessionPersona: Record<string, string>
  loadPersonas: () => Promise<void>
  setSessionPersona: (sessionId: string, personaId: string) => void
  getSessionPersona: (sessionId: string) => string
}

function loadSaved(sessionId: string): string {
  if (typeof window === 'undefined') return 'chat'
  try {
    const raw = localStorage.getItem(`${PERSONA_KEY}-${sessionId}`)
    return raw || 'chat'
  } catch {
    return 'chat'
  }
}

export const usePersonaStore = create<PersonaState>()((set, get) => ({
  personas: [],
  loaded: false,
  sessionPersona: {},
  loadPersonas: async () => {
    if (get().loaded) return
    try {
      const data = await api.personas.list()
      set({ personas: data.personas ?? [], loaded: true })
    } catch (err) {
      console.error('loadPersonas error:', err)
      set({ loaded: true })
    }
  },
  setSessionPersona: (sessionId, personaId) => {
    set((state) => ({ sessionPersona: { ...state.sessionPersona, [sessionId]: personaId } }))
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem(`${PERSONA_KEY}-${sessionId}`, personaId)
      } catch {
        /* ignore quota errors */
      }
    }
  },
  getSessionPersona: (sessionId) =>
    get().sessionPersona[sessionId] ?? loadSaved(sessionId),
}))