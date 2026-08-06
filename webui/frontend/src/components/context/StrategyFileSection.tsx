import { useState } from 'react'
import { FileCode, ChevronRight, ChevronDown } from 'lucide-react'
import type { StrategyFile } from '../../utils/contextExtractors'

/**
 * Displays the strategy files (strategy.py / config.yaml) that the
 * agent actually wrote/modified, with a collapsible diff between the
 * old and new content.
 *
 * Truthfulness L3: lets the user verify "what the agent claimed it
 * wrote" against the real file contents — the strategy file is the
 * source of truth, not the chat message.
 */
export function StrategyFileSection({ files }: { files: StrategyFile[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  if (files.length === 0) return null

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <FileCode className="h-3 w-3" />
        <span>策略文件</span>
        <span className="text-slate-600">({files.length})</span>
      </div>
      <div className="space-y-1">
        {files.map((f) => {
          const isOpen = expanded.has(f.path)
          const hasDiff = f.old_content !== f.new_content
          return (
            <div
              key={f.path}
              className="rounded border border-slate-800/50 bg-slate-900/30"
            >
              <button
                onClick={() => toggle(f.path)}
                className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-xs hover:bg-slate-800/60"
              >
                {isOpen ? (
                  <ChevronDown className="h-3 w-3 flex-shrink-0 text-slate-500" />
                ) : (
                  <ChevronRight className="h-3 w-3 flex-shrink-0 text-slate-500" />
                )}
                <FileCode className="h-3.5 w-3.5 flex-shrink-0 text-slate-500" />
                <span className="flex-1 truncate font-mono text-[11px] text-slate-300">
                  {f.path}
                </span>
                <span
                  className={`text-[10px] ${
                    f.status === 'created' ? 'text-emerald-400' : 'text-amber-400'
                  }`}
                >
                  {f.status === 'created' ? 'created' : 'modified'}
                </span>
              </button>
              {isOpen && (
                <div className="border-t border-slate-800/50 px-2 py-1.5">
                  {hasDiff ? (
                    <DiffView
                      oldContent={f.old_content}
                      newContent={f.new_content}
                    />
                  ) : (
                    <pre className="max-h-48 overflow-auto rounded bg-slate-950/60 p-2 text-[10px] leading-relaxed text-slate-300">
                      {f.new_content || '(empty)'}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Simple per-line diff: unchanged → slate, added → emerald, removed → red. */
function DiffView({
  oldContent,
  newContent,
}: {
  oldContent: string
  newContent: string
}) {
  const oldLines = oldContent.split('\n')
  const newLines = newContent.split('\n')
  const oldSet = new Set(oldLines.filter((l) => l.trim()))
  const newSet = new Set(newLines.filter((l) => l.trim()))

  return (
    <div className="max-h-48 overflow-auto rounded bg-slate-950/60 p-2 font-mono text-[10px] leading-relaxed">
      {newLines.map((line, i) => {
        const added = line.trim() && !oldSet.has(line)
        return (
          <div
            key={i}
            className={added ? 'bg-emerald-500/10 text-emerald-300' : 'text-slate-300'}
          >
            <span className="mr-1 select-none text-slate-600">{added ? '+' : ' '}</span>
            {line}
          </div>
        )
      })}
      {oldLines.map((line, i) => {
        const removed = line.trim() && !newSet.has(line)
        if (!removed) return null
        return (
          <div key={`old-${i}`} className="bg-red-500/10 text-red-300">
            <span className="mr-1 select-none text-slate-600">-</span>
            {line}
          </div>
        )
      })}
    </div>
  )
}