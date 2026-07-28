import { useLayoutStore } from '../../stores/layout'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
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