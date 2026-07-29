import { useEffect } from 'react'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { IconNav } from './IconNav'
import { TopBar } from './TopBar'
import { MainSplit } from './MainSplit'
import { RightPanel } from './RightPanel'
import { ToastManager } from '../common/Toast'
import { CommandPalette } from '../common/CommandPalette'
import { ErrorBoundary } from '../common/ErrorBoundary'
import { SearchModal } from '../common/SearchModal'

export function AppShell() {
  useKeyboardShortcuts()
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const loadSessions = useSessionStore((s) => s.loadSessions)

  // Init: restore from persisted state or create fresh
  useEffect(() => {
    let cancelled = false
    const init = async () => {
      try {
        // 1. Load all sessions metadata (await so filter below works)
        await loadSessions()
        if (cancelled) return

        // 2. Read latest persisted state AFTER loadSessions
        const state = useSessionStore.getState()
        const validOpenIds = (state.openSessionIds ?? []).filter((id) =>
          state.sessions.some((s) => s.id === id)
        )

        if (validOpenIds.length > 0) {
          // Re-open tabs that exist
          for (const id of validOpenIds) {
            try {
              await state.openSession(id)
            } catch {
              // 404 — session deleted server-side, skip
            }
            if (cancelled) return
          }
          // Switch to current (or first valid)
          const target =
            state.currentSessionId && validOpenIds.includes(state.currentSessionId)
              ? state.currentSessionId
              : validOpenIds[0]
          if (target && target !== state.currentSessionId) {
            await state.switchSession(target)
          } else if (target) {
            // Same session — just reload messages
            await useChatStore.getState().loadMessages(target)
          }
        } else {
          // No valid sessions — create one
          await state.createNewSession('新会话')
        }
      } catch (err) {
        console.error('AppShell init failed:', err)
        // Retry after a short delay
        if (!cancelled) setTimeout(init, 1000)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
    // Run only on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useSSE(currentSessionId)

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      <IconNav />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <div className="flex flex-1 overflow-hidden">
          <ErrorBoundary>
            <MainSplit />
          </ErrorBoundary>
          <ErrorBoundary>
            <RightPanel />
          </ErrorBoundary>
        </div>
      </div>
      <ToastManager />
      <CommandPalette />
      <SearchModal />
    </div>
  )
}