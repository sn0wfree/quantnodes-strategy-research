import { Search, Command, PanelRight, PanelRightClose } from 'lucide-react'
import { useCommandPaletteStore } from '../../stores/commandPalette'
import { useSessionStore } from '../../stores/session'
import { useLayoutStore } from '../../stores/layout'
import { SSEStatus } from '../common/SSEStatus'

export function TopBar() {
  const togglePalette = useCommandPaletteStore((s) => s.toggle)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const sessions = useSessionStore((s) => s.sessions)
  const currentSession = sessions.find((s) => s.id === currentSessionId)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)

  return (
    <header className="glass flex h-12 items-center justify-between border-b border-slate-800 px-4">
      <div className="flex items-center gap-3">
        <h1 className="text-sm font-medium text-slate-200">Strategy Research</h1>
        {currentSession && (
          <span className="text-xs text-slate-500">/ {currentSession.title}</span>
        )}
        <div className="ml-2 border-l border-slate-700 pl-2">
          <SSEStatus />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={toggleRightPanel}
          title={rightPanelVisible ? '隐藏右侧面板' : '显示右侧面板'}
          className="flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/50 px-2.5 py-1.5 text-xs text-slate-400 hover:border-slate-600 hover:text-slate-300"
        >
          {rightPanelVisible ? (
            <PanelRightClose className="h-3.5 w-3.5" />
          ) : (
            <PanelRight className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={togglePalette}
          className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-1.5 text-xs text-slate-400 hover:border-slate-600 hover:text-slate-300"
        >
          <Search className="h-3 w-3" />
          搜索
          <kbd className="ml-2 rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-500">
            <Command className="inline h-2.5 w-2.5" /> K
          </kbd>
        </button>
      </div>
    </header>
  )
}