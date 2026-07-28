import { useLayoutStore } from '../../stores/layout'
import { useKeyboardShortcuts } from '../../hooks/useKeyboardShortcuts'
import { useSSE } from '../../hooks/useSSE'
import { useSessionStore } from '../../stores/session'
import { IconNav } from './IconNav'
import { TopBar } from './TopBar'
import { MainSplit } from './MainSplit'
import { RightPanel } from './RightPanel'
import { ToastManager } from '../common/Toast'

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
          <MainSplit />
          <RightPanel />
        </div>
      </div>
      <ToastManager />
    </div>
  )
}
