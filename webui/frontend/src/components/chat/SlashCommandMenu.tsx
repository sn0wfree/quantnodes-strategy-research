import { useEffect, useMemo, useRef, useState } from 'react'
import { SLASH_COMMANDS } from './slashCommands'

interface SlashCommandMenuProps {
  query: string
  onSelect: (command: string) => void
}

export function SlashCommandMenu({ query, onSelect }: SlashCommandMenuProps) {
  const [activeIndex, setActiveIndex] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = useMemo(() => {
    const q = query.replace('/', '').toLowerCase()
    if (!q) return SLASH_COMMANDS
    return SLASH_COMMANDS.filter(
      (c) =>
        c.command.toLowerCase().includes(q) ||
        c.label.toLowerCase().includes(q),
    )
  }, [query])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    const el = listRef.current?.children[activeIndex] as HTMLElement | undefined
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [activeIndex])

  // Keyboard navigation driven from the Composer (Arrow/Enter overrides).
  useEffect(() => {
    const onNav = (e: Event) => {
      const detail = (e as CustomEvent).detail ?? 1
      setActiveIndex((i) => {
        const next = i + detail
        if (next < 0) return filtered.length - 1
        if (next >= filtered.length) return 0
        return next
      })
    }
    const onEnter = () => {
      const cmd = filtered[activeIndex]
      if (cmd) onSelect(cmd.command)
    }
    window.addEventListener('sr:slash-nav', onNav)
    window.addEventListener('sr:slash-enter', onEnter)
    return () => {
      window.removeEventListener('sr:slash-nav', onNav)
      window.removeEventListener('sr:slash-enter', onEnter)
    }
  }, [filtered, activeIndex, onSelect])

  if (filtered.length === 0) {
    return (
      <div className="glass absolute bottom-full left-0 z-50 mb-2 w-72 rounded-xl border border-slate-700/60 p-3 text-sm text-slate-400 shadow-xl">
        无匹配命令
      </div>
    )
  }

  return (
    <div className="glass absolute bottom-full left-0 z-50 mb-2 w-80 overflow-hidden rounded-xl border border-slate-700/60 shadow-2xl">
      <ul ref={listRef} className="max-h-72 overflow-y-auto py-1">
        {filtered.map((cmd, i) => (
          <li key={cmd.command}>
            <button
              type="button"
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => onSelect(cmd.command)}
              className={`flex w-full items-center gap-3 px-3 py-2 text-left transition-colors ${
                i === activeIndex
                  ? 'bg-slate-700/40 text-slate-100'
                  : 'text-slate-300 hover:bg-slate-700/30'
              }`}
            >
              <span className="text-primary-400">{cmd.icon}</span>
              <span className="flex-1">
                <span className="block text-sm font-medium">
                  <span className="text-primary-400">{cmd.command}</span>
                  <span className="ml-2 text-slate-400">{cmd.label}</span>
                </span>
                <span className="block text-xs text-slate-500">
                  {cmd.description}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}