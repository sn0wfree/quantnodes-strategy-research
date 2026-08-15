import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '../api/client'

export interface User {
  id: string
  username: string
  display_name: string
  role?: string
  is_active?: boolean
}

interface AuthState {
  token: string | null
  user: User | null
  setAuth: (token: string, user: User) => void
  setUser: (user: User) => void
  logout: () => void
  refreshMe: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      setUser: (user) => set({ user }),
      logout: () => set({ token: null, user: null }),
      refreshMe: async () => {
        if (!get().token) return
        try {
          const me = await api.get<{
            id: string
            username: string
            display_name: string
            role: string
            is_active?: boolean
          }>('/auth/me')
          set({ user: me })
        } catch {
          // Ignore transient failures; keep last-known user.
        }
      },
    }),
    { name: 'sr-auth' }
  )
)