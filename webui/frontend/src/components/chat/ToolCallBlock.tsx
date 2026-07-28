import { useState } from 'react'
import {
  Wrench, CheckCircle, XCircle, ChevronDown, ChevronRight,
  Clock, Copy, Check,
} from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'

interface ToolCallBlockProps {
  toolCall: ToolCallPart
  startTime?: number
}

const STATUS_CONFIG = {
  pending: { icon: Wrench, color: 'text-slate-500', bg: 'bg-slate-500/10' },
  running: { icon: Wrench, color: 'text-amber-400', bg: 'bg-amber-500/10' },
  done: { icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
  error: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10' },
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatResultPreview(result: string): string {
  if (!result) return ''
  try {
    const parsed = JSON.parse(result)
    if (typeof parsed === 'object') {
      const keys = Object.keys(parsed)
      if (keys.length <= 3) return keys.join(', ')
      return `${keys.slice(0, 3).join(', ')} +${keys.length - 3}`
    }
    return String(parsed).slice(0, 80)
  } catch {
    return result.slice(0, 80)
  }
}

export function ToolCallBlock({ toolCall, startTime }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState<'args' | 'result' | null>(null)

  const config = STATUS_CONFIG[toolCall.status]
  const Icon = config.icon
  const duration = startTime ? Date.now() - startTime : null

  let argsPreview = ''
  try {
    const parsed = JSON.parse(toolCall.arguments)
    argsPreview = Object.keys(parsed).join(', ')
  } catch {
    argsPreview = toolCall.arguments.slice(0, 60)
  }

  const handleCopy = (text: string, which: 'args' | 'result') => {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className={`my-1.5 rounded-lg border overflow-hidden transition-colors ${
      expanded ? 'border-slate-600' : 'border-slate-700/50'
    }`}>
      {/* Header row */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-800/50 transition-colors ${config.bg}`}
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-slate-500" />
        ) : (
          <ChevronRight className="h-3 w-3 text-slate-500" />
        )}
        <Icon className={`h-3.5 w-3.5 ${config.color}`} />
        <span className="font-mono font-medium text-slate-200">{toolCall.name}</span>
        {argsPreview && (
          <span className="text-slate-500 truncate max-w-[200px]">
            ({argsPreview})
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5 text-slate-600">
          {duration !== null && (
            <span className="flex items-center gap-0.5">
              <Clock className="h-3 w-3" />
              {formatDuration(duration)}
            </span>
          )}
          {toolCall.status === 'running' && (
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
          )}
        </span>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-slate-700/50 p-3 space-y-2">
          {/* Arguments */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">参数</span>
              <button
                onClick={() => handleCopy(toolCall.arguments, 'args')}
                className="text-slate-600 hover:text-slate-400"
              >
                {copied === 'args' ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              </button>
            </div>
            <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all bg-slate-800/50 rounded p-2">
              {formatJson(toolCall.arguments)}
            </pre>
          </div>

          {/* Result */}
          {toolCall.result && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">结果</span>
                <button
                  onClick={() => handleCopy(toolCall.result!, 'result')}
                  className="text-slate-600 hover:text-slate-400"
                >
                  {copied === 'result' ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                </button>
              </div>
              <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all bg-slate-800/50 rounded p-2 max-h-60 overflow-y-auto">
                {formatJson(toolCall.result)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatJson(str: string): string {
  try {
    return JSON.stringify(JSON.parse(str), null, 2)
  } catch {
    return str
  }
}
