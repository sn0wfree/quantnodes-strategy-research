import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
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
import { SessionSidebar } from '../chat/SessionSidebar'
import { ToastManager } from '../common/Toast'
import { CommandPalette } from '../common/CommandPalette'
import { SearchModal } from '../common/SearchModal'
import { PermissionRequestDialog } from '../chat/PermissionRequestDialog'
import { useToastStore } from '../../stores/toast'
import { api } from '../../api/client'

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
          state.sessions.some((s) => s.id === id) && !id.startsWith('study:')
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
            // Same session — just reload messages and backfill
            // Agent / DAG / Goal panels (loadSessionState). The original
            // code only reloaded messages, leaving the right panel
            // empty until the next run started (B13 follow-up; see
            // session.ts loadSessionState docstring).
            await Promise.all([
              useChatStore.getState().loadMessages(target),
              state.loadSessionState(target),
            ])
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

  const location = useLocation()
  // Route-level SSE isolation: /dag renders its own orchestrator session,
  // so AppShell must NOT also subscribe to the main chat session — that
  // would create two EventSources writing into the same global chatStore
  // and double the status indicator. The orchestrator panel mounts its
  // own useSSE(sessionId='dag:xxx') inside /dag.
  const isDagRoute = location.pathname.startsWith('/dag')
  useSSE(isDagRoute ? null : currentSessionId)

  const sidebarOpen = useLayoutStore((s) => s.sidebarOpen)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
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
                visible columns always tile edge-to-edge with no gap. */}
            <div className="flex flex-1 min-w-0">
              <MainSplit />
            </div>
            {rightPanelVisible && (
              <>
                {/* Divider: dragging TOWARD right (delta < 0) grows the
                    panel; chat (flex-1) absorbs the difference. */}
                <SplitDivider
                  onDrag={(delta) => {
                    useLayoutStore.setState((s) => ({
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
      <PermissionRequestDialogBridge />
    </div>
  )
}

/**
 * Bridge component: wires the chat store's `pendingPermission` slot
 * to the dialog's `onRespond` callback (which posts to the backend
 * gateway endpoint). Kept inline so the dialog is only rendered when
 * there is a real pending request, and so the `api` import is not
 * pulled into the dialog module (single responsibility).
 */
function PermissionRequestDialogBridge() {
  const pending = useChatStore((s) => s.pendingPermission)
  const clearPending = useChatStore.setState
  const addToast = useToastStore((s) => s.addToast)

  if (!pending) return null

  const handleRespond = async (
    action: 'allow' | 'deny',
    permanent: boolean,
  ) => {
    // Optimistically close the dialog — the SSE permission_result
    // event (if the gateway emits one) will be a no-op since the
    // store has already cleared the slot.
    clearPending({ pendingPermission: null })
    try {
      const res = await api.permission.respond({
        tool_call_id: pending.tool_call_id,
        action,
        permanent,
      })
      if (res.status === 'expired') {
        addToast('warning', '权限请求已过期（操作可能已超时）')
      } else if (res.status === 'ok') {
        addToast(
          'success',
          permanent
            ? `已${action === 'allow' ? '始终允许' : '始终拒绝'} ${pending.tool_name}`
            : `已${action === 'allow' ? '允许' : '拒绝'}本次`,
        )
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '未知错误'
      addToast('error', `权限请求失败：${msg}`)
    }
  }

  return (
    <PermissionRequestDialog request={pending} onRespond={handleRespond} />
  )
}