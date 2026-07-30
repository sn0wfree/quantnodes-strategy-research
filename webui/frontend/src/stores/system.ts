import { create } from 'zustand'

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
      const res = await fetch('/api/system/info')
      if (!res.ok) return
      const data = await res.json()
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