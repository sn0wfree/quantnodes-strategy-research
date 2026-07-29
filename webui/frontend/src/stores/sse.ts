import { create } from 'zustand'

export type SSEConnectionStatus = 'connecting' | 'connected' | 'disconnected'

interface SSEState {
  status: SSEConnectionStatus
  setStatus: (status: SSEConnectionStatus) => void
}

export const useSSEStore = create<SSEState>()((set) => ({
  status: 'disconnected',
  setStatus: (status) => set({ status }),
}))
