import { create } from 'zustand'
import { api } from '../api/client'

interface LLMInfo {
  provider: string
  model: string
  configured: boolean
}

interface SystemState {
  llm: LLMInfo
  fetchSystemInfo: () => Promise<void>
}

const initialLLM: LLMInfo = {
  provider: '',
  model: '',
  configured: false,
}

export const useSystemStore = create<SystemState>((set) => ({
  llm: initialLLM,
  fetchSystemInfo: async () => {
    try {
      const data = await api.get<{ llm: { provider: string; model: string; configured: boolean } }>('/system/info')
      const llm = data?.llm
      if (!llm) return
      set({
        llm: {
          provider: llm.provider ?? '',
          model: llm.model ?? '',
          configured: !!llm.configured,
        },
      })
    } catch {
      // Silent: keep defaults
    }
  },
}))