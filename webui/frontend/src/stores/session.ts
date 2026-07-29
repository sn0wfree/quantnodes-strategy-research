import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '../api/client'
import type { Message } from './chat'
import { useChatStore } from './chat'

export interface Session {
  id: string
  user_id?: string
  title: string
  created_at: number
  updated_at: number
  starred: boolean
  tags: string[]
  message_count: number
  archived: boolean
}

export interface SearchHit {
  session_id: string
  session_title: string
  message_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  snippet: string
  score: number
  created_at: number
}

interface SessionState {
  sessions: Session[]
  openSessionIds: string[]
  currentSessionId: string | null
  searchResults: SearchHit[]
  searchOpen: boolean
  searchQuery: string
  isSearching: boolean

  // Existing
  setSessions: (sessions: Session[]) => void
  setCurrentSession: (id: string | null) => void
  addSession: (session: Session) => void
  removeSession: (id: string) => void

  // New
  loadSessions: () => Promise<void>
  switchSession: (id: string) => Promise<void>
  createNewSession: (title?: string) => Promise<Session>
  openSession: (id: string) => Promise<void>
  closeSession: (id: string) => void
  updateSessionMeta: (id: string, partial: Partial<Session>) => Promise<void>
  deleteSession: (id: string) => Promise<void>
  setSearchOpen: (open: boolean) => void
  runSearch: (query: string) => Promise<void>
  clearSearch: () => void
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessions: [],
      openSessionIds: [],
      currentSessionId: null,
      searchResults: [],
      searchOpen: false,
      searchQuery: '',
      isSearching: false,

      setSessions: (sessions) => set({ sessions }),
      setCurrentSession: (id) => set({ currentSessionId: id }),
      addSession: (session) =>
        set((s) => ({
          sessions: [session, ...s.sessions.filter((x) => x.id !== session.id)],
        })),
      removeSession: (id) =>
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.id !== id),
          currentSessionId: s.currentSessionId === id ? null : s.currentSessionId,
          openSessionIds: s.openSessionIds.filter((sid) => sid !== id),
        })),

      loadSessions: async () => {
        try {
          const res = await api.get<{ sessions: Session[] }>('/chat/session')
          set({ sessions: res.sessions })
        } catch (err) {
          console.error('loadSessions failed:', err)
        }
      },

      switchSession: async (id: string) => {
        const { openSessionIds, currentSessionId } = get()
        if (currentSessionId === id) return

        // Add to openSessionIds if not present
        if (!openSessionIds.includes(id)) {
          set({ openSessionIds: [...openSessionIds, id] })
        }
        set({ currentSessionId: id })

        // Clear current messages and load target session messages
        const chat = useChatStore.getState()
        chat.setMessages([])
        chat.setStreamingMessage(null)
        chat.setStreamingText('')
        await chat.loadMessages(id)
      },

      createNewSession: async (title = '新会话') => {
        const session = await api.post<Session>('/chat/session', { title })
        get().addSession(session)
        set((s) => ({
          openSessionIds: s.openSessionIds.includes(session.id)
            ? s.openSessionIds
            : [...s.openSessionIds, session.id],
          currentSessionId: session.id,
        }))
        // Clear chat for new session
        const chat = useChatStore.getState()
        chat.setMessages([])
        chat.setStreamingMessage(null)
        chat.setStreamingText('')
        return session
      },

      openSession: async (id: string) => {
        const { openSessionIds, sessions } = get()
        if (openSessionIds.includes(id)) {
          // Already open — just switch
          await get().switchSession(id)
          return
        }
        // Load session metadata if not present
        if (!sessions.find((s) => s.id === id)) {
          try {
            const session = await api.get<Session>(`/chat/session/${id}`)
            get().addSession(session)
          } catch (err) {
            console.error('openSession metadata failed:', err)
            throw err
          }
        }
        set({ openSessionIds: [...openSessionIds, id] })
        await get().switchSession(id)
      },

      closeSession: (id: string) => {
        const { openSessionIds, currentSessionId } = get()
        const newOpen = openSessionIds.filter((sid) => sid !== id)
        set({ openSessionIds: newOpen })

        if (currentSessionId !== id) return

        // Pick neighbor: next tab in current order, or previous
        const idx = openSessionIds.indexOf(id)
        const neighbor = newOpen[idx] || newOpen[idx - 1] || newOpen[0] || null
        if (neighbor) {
          // switchSession sets currentSessionId and loads messages
          void get().switchSession(neighbor)
        } else {
          // No tabs left — clear current
          set({ currentSessionId: null })
          const chat = useChatStore.getState()
          chat.setMessages([])
          chat.setStreamingMessage(null)
          chat.setStreamingText('')
        }
      },

      updateSessionMeta: async (id, partial) => {
        // Optimistic update
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === id ? { ...sess, ...partial } : sess
          ),
        }))
        try {
          const updated = await api.patch<Session>(`/chat/session/${id}`, partial)
          set((s) => ({
            sessions: s.sessions.map((sess) =>
              sess.id === id ? updated : sess
            ),
          }))
        } catch (err) {
          console.error('updateSessionMeta failed:', err)
          // Re-fetch to recover
          await get().loadSessions()
          throw err
        }
      },

      deleteSession: async (id: string) => {
        try {
          await api.delete(`/chat/session/${id}`)
        } catch (err) {
          console.error('deleteSession failed:', err)
          throw err
        }
        // Use removeSession to clean up locally
        get().removeSession(id)
        // If the deleted session was current, pick a neighbor
        if (get().currentSessionId === null) {
          const next = get().openSessionIds[0] || null
          if (next) {
            await get().switchSession(next)
          } else {
            // No tabs left — create a fresh session
            await get().createNewSession('新会话')
          }
        }
      },

      setSearchOpen: (open) => set({ searchOpen: open }),

      runSearch: async (query: string) => {
        set({ searchQuery: query })
        if (!query.trim()) {
          set({ searchResults: [], isSearching: false })
          return
        }
        set({ isSearching: true })
        try {
          const res = await api.post<{ hits: SearchHit[] }>('/chat/session/search', {
            query,
            limit: 20,
          })
          set({ searchResults: res.hits })
        } catch (err) {
          console.error('runSearch failed:', err)
          set({ searchResults: [] })
        } finally {
          set({ isSearching: false })
        }
      },

      clearSearch: () => set({ searchResults: [], searchQuery: '' }),
    }),
    {
      name: 'sr-sessions',
      partialize: (state) => ({
        openSessionIds: state.openSessionIds,
        currentSessionId: state.currentSessionId,
      }),
    }
  )
)

export type { Message }