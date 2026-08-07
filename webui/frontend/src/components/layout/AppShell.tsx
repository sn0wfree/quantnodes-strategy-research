import { useEffect } from 'react'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useLayoutStore } from '../../stores/layout'
import { IconNav } from './IconNav'
import { TopBar } from './TopBar'
import { MainSplit } from './MainSplit'
import { RightPanel } from './RightPanel'
import { SplitDivider } from './SplitDivider'
import { ContextPanel } from '../context/ContextPanel'
import { SessionSidebar } from '../chat/SessionSidebar'
import { ToastManager } from '../common/Toast'
import { CommandPalette } from '../common/CommandPalette'
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
          // Re-open tabs in PARALLEL (was sequential await per tab —
          // N round-trips on boot; each openSession loads messages).
          const results = await Promise.allSettled(
            validOpenIds.map((id) => state.openSession(id))
          )
          results.forEach((r, i) => {
            if (r.status === 'rejected') {
              // 404 — session deleted server-side, skip
              console.debug('openSession rejected:', validOpenIds[i], r.reason)
            }
          })
          if (cancelled) return
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

  const sidebarOpen = useLayoutStore((s) => s.sidebarOpen)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const contextRatio = useLayoutStore((s) => s.contextRatio)
  const rightRatio = useLayoutStore((s) => s.rightRatio)

  return (
    <div className="relative flex h-screen overflow-hidden bg-app">
      {/* Ambient backdrop: grid + aurora + vignette + grain */}
      <div className="aurora-backdrop">
        <div className="grid-layer" />
        <div className="aurora-layer" />
        <div className="vignette-layer" />
        <div className="grain-layer" />
      </div>
      <div className="relative z-10 flex h-full w-full overflow-hidden">
        <IconNav />
        {sidebarOpen && <SessionSidebar />}
        <div className="flex flex-1 flex-col overflow-hidden">
          <TopBar />
          <div className="flex flex-1 overflow-hidden">
            {/* Left chat column — flex-1 absorbs all leftover width so the
                three columns always tile edge-to-edge with no gap. */}
            <div className="flex flex-1 min-w-0">
              <MainSplit />
            </div>
            {/* Left divider: dragging TOWARD chat (delta < 0) grows
                context; chat (flex-1) absorbs the difference. */}
            <SplitDivider
              onDrag={(delta) => {
                useLayoutStore.setState((s) => ({
                  contextRatio: s.contextRatio - delta,
                }))
              }}
            />
            <div
              className="flex h-full flex-shrink-0 overflow-hidden"
              style={{ width: `${contextRatio * 100}%` }}
            >
              <ContextPanel />
            </div>
            {rightPanelVisible && (
              <>
                {/* Right divider: dragging TOWARD right (delta < 0) grows
                    right; context shrinks by the same amount; chat
                    (flex-1) is unchanged because contextRatio + rightRatio
                    stays constant. */}
                <SplitDivider
                  onDrag={(delta) => {
                    useLayoutStore.setState((s) => ({
                      contextRatio: s.contextRatio + delta,
                      rightRatio: s.rightRatio - delta,
                    }))
                  }}
                />
                <div
                  className="flex h-full flex-shrink-0 overflow-hidden"
                  style={{ width: `${rightRatio * 100}%` }}
                >
                  <RightPanel />
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      <ToastManager />
      <CommandPalette />
      <SearchModal />
    </div>
  )
}