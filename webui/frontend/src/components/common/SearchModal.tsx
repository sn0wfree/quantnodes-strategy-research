import { useState, useEffect, useRef } from 'react'
import { Search, X, User, Bot, FileText } from 'lucide-react'
import { useSessionStore, type SearchHit } from '../../stores/session'

export function SearchModal() {
  const open = useSessionStore((s) => s.searchOpen)
  const setOpen = useSessionStore((s) => s.setSearchOpen)
  const query = useSessionStore((s) => s.searchQuery)
  const results = useSessionStore((s) => s.searchResults)
  const runSearch = useSessionStore((s) => s.runSearch)
  const clearSearch = useSessionStore((s) => s.clearSearch)
  const openSession = useSessionStore((s) => s.openSession)

  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // Focus input on open
  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus()
      setSelectedIdx(0)
    }
  }, [open])

  // Debounced search
  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => {
      void runSearch(query)
    }, 200)
    return () => clearTimeout(timer)
  }, [query, open, runSearch])

  // Reset on close
  useEffect(() => {
    if (!open) clearSearch()
  }, [open, clearSearch])

  const handleSelect = async (hit: SearchHit) => {
    setOpen(false)
    try {
      await openSession(hit.session_id)
      // Scroll to message after a short delay (after messages are loaded)
      setTimeout(() => {
        const el = document.getElementById(`msg-${hit.message_id}`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          // Brief highlight
          el.classList.add('ring-2', 'ring-primary-500', 'ring-offset-2', 'ring-offset-slate-900')
          setTimeout(() => {
            el.classList.remove('ring-2', 'ring-primary-500', 'ring-offset-2', 'ring-offset-slate-900')
          }, 1500)
        }
      }, 300)
    } catch (err) {
      console.error('Failed to open session:', err)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const hit = results[selectedIdx]
      if (hit) void handleSelect(hit)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 backdrop-blur-sm pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-800 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 border-b border-slate-700 px-4 py-3">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              useSessionStore.setState({ searchQuery: e.target.value })
            }}
            onKeyDown={handleKeyDown}
            placeholder="搜索消息内容（支持中文/英文）..."
            className="flex-1 bg-transparent text-sm text-slate-100 placeholder-slate-500 outline-none"
          />
          <button
            onClick={() => setOpen(false)}
            className="flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-slate-700 hover:text-slate-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Results */}
        <div className="max-h-[60vh] overflow-y-auto p-2">
          {!query.trim() ? (
            <div className="py-12 text-center text-sm text-slate-500">
              输入关键词搜索所有会话的消息
            </div>
          ) : results.length === 0 ? (
            <div className="py-12 text-center text-sm text-slate-500">
              未找到匹配结果
            </div>
          ) : (
            results.map((hit, idx) => (
              <button
                key={`${hit.session_id}-${hit.message_id}`}
                onClick={() => void handleSelect(hit)}
                onMouseEnter={() => setSelectedIdx(idx)}
                className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors
                  ${idx === selectedIdx ? 'bg-primary-600/20' : 'hover:bg-slate-700/50'}
                `}
              >
                <RoleIcon role={hit.role} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-medium text-slate-200 truncate">
                      {hit.session_title}
                    </span>
                    <span className="text-slate-500">·</span>
                    <span className="text-slate-500 text-[10px]">
                      {new Date(hit.created_at * 1000).toLocaleString('zh-CN', {
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </span>
                  </div>
                  <div
                    className="mt-1 text-xs text-slate-400 line-clamp-2 [&_mark]:bg-amber-500/30 [&_mark]:text-amber-200 [&_mark]:rounded-sm [&_mark]:px-0.5"
                    dangerouslySetInnerHTML={{ __html: hit.snippet }}
                  />
                </div>
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-slate-700 px-4 py-2 text-[10px] text-slate-500">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-slate-700 px-1 py-0.5">↑</kbd>
              <kbd className="rounded border border-slate-700 px-1 py-0.5">↓</kbd>
              导航
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-slate-700 px-1 py-0.5">↵</kbd>
              打开
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-slate-700 px-1 py-0.5">ESC</kbd>
              关闭
            </span>
          </div>
          <span>{results.length} 个结果</span>
        </div>
      </div>
    </div>
  )
}

function RoleIcon({ role }: { role: SearchHit['role'] }) {
  if (role === 'user') {
    return (
      <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary-600/20 text-primary-400">
        <User className="h-3 w-3" />
      </div>
    )
  }
  if (role === 'assistant') {
    return (
      <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-emerald-600/20 text-emerald-400">
        <Bot className="h-3 w-3" />
      </div>
    )
  }
  return (
    <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-slate-700 text-slate-400">
      <FileText className="h-3 w-3" />
    </div>
  )
}