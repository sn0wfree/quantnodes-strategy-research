import { create } from 'zustand'
import { api } from '../api/client'

export interface ProviderModel {
  name: string
  label: string
  model: string
  models: string[]
  base_url: string
  key_var: string
  key_configured: boolean
}

interface ModelState {
  /** Provider catalog from /system/llm */
  providers: ProviderModel[]
  /** Per-session model override: sessionId → "provider/model" */
  sessionModels: Record<string, string>
  /** Load provider catalog from backend */
  loadProviders: () => Promise<void>
  /** Set model override for a session */
  setSessionModel: (sessionId: string, model: string) => void
  /** Get model override for a session (null = use default) */
  getModelForSession: (sessionId: string) => string | null
  /** Clear model override for a session */
  clearSessionModel: (sessionId: string) => void
}

function loadPersistedModels(): Record<string, string> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = localStorage.getItem('sr-session-models')
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function persistModels(models: Record<string, string>) {
  if (typeof window !== 'undefined') {
    localStorage.setItem('sr-session-models', JSON.stringify(models))
  }
}

export const useModelStore = create<ModelState>()((set, get) => ({
  providers: [],
  sessionModels: loadPersistedModels(),
  loadProviders: async () => {
    try {
      const data = await api.get<{ providers?: ProviderModel[] }>('/system/llm')
      if (data?.providers) {
        set({ providers: data.providers })
      }
    } catch {
      // Silent: keep empty providers
    }
  },
  setSessionModel: (sessionId, model) => {
    const next = { ...get().sessionModels, [sessionId]: model }
    persistModels(next)
    set({ sessionModels: next })
  },
  getModelForSession: (sessionId) => {
    return get().sessionModels[sessionId] ?? null
  },
  clearSessionModel: (sessionId) => {
    const next = { ...get().sessionModels }
    delete next[sessionId]
    persistModels(next)
    set({ sessionModels: next })
  },
}))
