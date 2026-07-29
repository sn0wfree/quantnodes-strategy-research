import { useState, useRef, useEffect } from 'react'
import { Search, Command, PanelRight, PanelRightClose, Pencil } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useLayoutStore } from '../../stores/layout'
import { SSEStatus } from '../common/SSEStatus'

export function TopBar() {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const sessions = useSessionStore((s) => s.sessions)
  const updateSessionMeta = useSessionStore((s) => s.updateSessionMeta)
  const setSearchOpen = useSessionStore((s) => s.setSearchOpen)
  const currentSession = sessions.find((s) => s.id === currentSessionId)
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)
  const toggleRightPanel = useLayoutStore((s) => s.toggleRightPanel)

  const [editing, setEditing] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editing])

  const startEdit = () => {
    if (!currentSession) return
    setDraftTitle(currentSession.title)
    setEditing(true)
  }

  const commit = async () => {
    const trimmed = draftTitle.trim()
    if (trimmed && currentSession && trimmed !== currentSession.title) {
      try {
        await updateSessionMeta(currentSession.id, { title: trimmed })
      } catch (err) {
        console.error('TopBar title save failed', err)
      }
    }
    setEditing(false)
  }

  const cancel = () => setEditing(false)

  return (
    <header className="glass flex h-12 items-center justify-between border-b border-slate-800 px-4">
      <div className="flex items-center gap-3 min-w-0">
        <h1 className="text-sm font-medium text-slate-200 flex-shrink-0">Strategy Research</h1>
        {currentSession && (
          <div className="flex items-center gap-1 min-w-0">
            <span className="text-xs text-slate-500 flex-shrink-0">/</span>
            {editing ? (
              <input
                ref={inputRef}
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                onBlur={() => void commit()}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    void commit()
                  } else if (e.key === 'Escape') {
                    e.preventDefault()
                    cancel()
                  }
                }}
                className="w-48 rounded border border-primary-500 bg-slate-900 px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                maxLength={80}
              />
            ) : (
              <>
                <span
                  className="truncate text-xs text-slate-300 max-w-[240px]"
                  title={currentSession.title}
                >
                  {currentSession.title}
                </span>
                <button
                  onClick={startEdit}
                  className="flex h-5 w-5 items-center justify-center rounded text-slate-500 hover:bg-slate-800 hover:text-slate-300 flex-shrink-0"
                  title="重命名"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </>
            )}
          </div>
        )}
        <div className="ml-2 border-l border-slate-700 pl-2 flex-shrink-0">
          <SSEStatus />
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
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
          onClick={() => setSearchOpen(true)}
          className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-1.5 text-xs text-slate-400 hover:border-slate-600 hover:text-slate-300"
        >
          <Search className="h-3 w-3" />
          搜索消息
          <kbd className="ml-2 rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-500">
            <Command className="inline h-2.5 w-2.5" /> K
          </kbd>
        </button>
      </div>
    </header>
  )
}