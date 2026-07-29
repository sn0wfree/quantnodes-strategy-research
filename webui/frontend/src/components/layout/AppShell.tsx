import { useEffect } from 'react'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
import { api } from '../../api/client'
import { IconNav } from './IconNav'
import { TopBar } from './TopBar'
import { MainSplit } from './MainSplit'
import { RightPanel } from './RightPanel'
import { ToastManager } from '../common/Toast'
import { CommandPalette } from '../common/CommandPalette'
import { ErrorBoundary } from '../common/ErrorBoundary'

export function AppShell() {
  useKeyboardShortcuts()
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const setSessions = useSessionStore((s) => s.setSessions)
  const setCurrentSession = useSessionStore((s) => s.setCurrentSession)
  const addSession = useSessionStore((s) => s.addSession)

  // Auto-load or create session on mount
  useEffect(() => {
    if (currentSessionId) return // already have a session

    const init = async () => {
      try {
        const res = await api.get<{ sessions: any[] }>('/chat/session')
        if (res.sessions && res.sessions.length > 0) {
          setSessions(res.sessions)
          setCurrentSession(res.sessions[0].id)
        } else {
          // Create first session
          const newSession = await api.post<any>('/chat/session', { title: '新会话' })
          addSession(newSession)
          setCurrentSession(newSession.id)
        }
      } catch {
        // API not ready yet, retry after a short delay
        setTimeout(init, 1000)
      }
    }
    init()
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
    </div>
  )
}