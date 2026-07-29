import { useState, useEffect } from 'react'
import {
  CheckCircle, XCircle, Loader2, ChevronRight,
  Clock, Copy, Check,
} from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'
import { MarkdownRenderer } from './MarkdownRenderer'

interface ToolCallBlockProps {
  toolCall: ToolCallPart
  startTime?: number
}

const STATUS_CONFIG = {
  pending: { icon: Loader2, color: 'text-slate-400', bg: 'bg-slate-700/30', dot: 'bg-slate-400' },
  running: { icon: Loader2, color: 'text-amber-400', bg: 'bg-amber-500/5', dot: 'bg-amber-400' },
  done: { icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-500/5', dot: 'bg-emerald-400' },
  error: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/5', dot: 'bg-red-400' },
} as const

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Try to extract a brief, single-line preview from JSON args. */
function summarizeArgs(args: string | unknown): string {
  // args may already be an object (loaded from DB) or a JSON string (from SSE)
  const parsed: Record<string, unknown> =
    typeof args === 'string'
      ? (() => {
          try {
            return JSON.parse(args) as Record<string, unknown>
          } catch {
            return {}
          }
        })()
      : ((args ?? {}) as Record<string, unknown>)
  const keys = Object.keys(parsed)
  if (keys.length === 0) {
    // Fall back to raw string representation
    if (typeof args === 'string') {
      return args.length > 30 ? args.slice(0, 30) + '…' : args
    }
    return ''
  }
  // First 2 keys with truncated values
  return keys
    .slice(0, 2)
    .map((k) => {
      const v = parsed[k]
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}: ${s.length > 24 ? s.slice(0, 24) + '…' : s}`
    })
    .join(', ')
}

/** Render args and result as readable markdown. */
function buildMarkdown(args: string | unknown, result: string | unknown | undefined): string {
  const argsStr = typeof args === 'string' ? args : JSON.stringify(args ?? {}, null, 2)
  let md = '**Arguments**\n\n```json\n' + argsStr + '\n```\n'
  if (result !== undefined) {
    const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
    md += '\n**Result**\n\n```json\n' + resultStr + '\n```\n'
  }
  return md
}

export function ToolCallBlock({ toolCall, startTime }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [tickMs, setTickMs] = useState(0)

  // While running, periodically refresh elapsed time for the duration chip.
  useEffect(() => {
    if (toolCall.status !== 'running' || !startTime) return
    const id = setInterval(() => setTickMs(Date.now() - startTime), 100)
    return () => clearInterval(id)
  }, [toolCall.status, startTime])

  const config = STATUS_CONFIG[toolCall.status]
  const Icon = config.icon
  const argsPreview = summarizeArgs(toolCall.arguments)
  const duration =
    toolCall.status === 'running' && startTime
      ? tickMs || Date.now() - startTime
      : startTime
      ? tickMs
      : null

  const handleCopyAll = (e: React.MouseEvent) => {
    e.stopPropagation()
    const text = buildMarkdown(toolCall.arguments, toolCall.result)
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div
      className={`my-1 border-l-2 rounded-r-md overflow-hidden transition-colors ${
        toolCall.status === 'running'
          ? 'border-amber-500/50 bg-amber-500/5'
          : toolCall.status === 'error'
          ? 'border-red-500/40 bg-red-500/5'
          : toolCall.status === 'done'
          ? 'border-emerald-500/30 bg-emerald-500/5'
          : 'border-slate-700 bg-slate-800/30'
      }`}
    >
      {/* Single-line summary (always visible) */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded((v) => !v)
          }
        }}
        className="group flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-white/[0.03] transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-slate-500 transition-transform ${
            expanded ? 'rotate-90' : ''
          }`}
        />
        <Icon
          className={`h-3.5 w-3.5 ${config.color} ${
            toolCall.status === 'running' ? 'animate-spin' : ''
          }`}
        />
        <span className="font-mono font-medium text-slate-200">
          {toolCall.name}
        </span>
        {argsPreview && (
          <span className="text-slate-500 truncate font-mono text-[11px] max-w-[260px]">
            {argsPreview}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {duration !== null && (
            <span className="flex items-center gap-0.5 font-mono">
              <Clock className="h-2.5 w-2.5" />
              {formatDuration(duration)}
            </span>
          )}
          <button
            type="button"
            onClick={handleCopyAll}
            className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-slate-300"
            title="复制 args + result"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </span>
      </div>

      {/* Expanded: markdown-rendered args + result */}
      {expanded && (
        <div className="border-t border-slate-700/40 px-3 py-2">
          <MarkdownRenderer
            content={buildMarkdown(toolCall.arguments, toolCall.result)}
          />
        </div>
      )}
    </div>
  )
}