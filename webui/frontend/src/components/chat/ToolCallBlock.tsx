import { useState } from 'react'
import { Wrench, CheckCircle, XCircle, ChevronDown, ChevronRight } from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'

interface ToolCallBlockProps {
  toolCall: ToolCallPart
}

const STATUS_ICON = {
  pending: <Wrench className="h-3.5 w-3.5 text-slate-500" />,
  running: <Wrench className="h-3.5 w-3.5 text-amber-400 animate-pulse" />,
  done: <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />,
  error: <XCircle className="h-3.5 w-3.5 text-red-400" />,
}

export function ToolCallBlock({ toolCall }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false)

  // Try to parse arguments for preview
  let argsPreview = toolCall.arguments
  try {
    const parsed = JSON.parse(toolCall.arguments)
    argsPreview = Object.keys(parsed).join(', ')
  } catch {
    // Keep raw
  }

  return (
    <div className="my-1.5 rounded-lg border border-slate-700/50 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 bg-slate-800/50 px-3 py-2 text-left text-xs hover:bg-slate-800/80 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-slate-500" />
        ) : (
          <ChevronRight className="h-3 w-3 text-slate-500" />
        )}
        {STATUS_ICON[toolCall.status]}
        <span className="font-mono text-slate-300">{toolCall.name}</span>
        {argsPreview && (
          <span className="text-slate-500 truncate">({argsPreview})</span>
        )}
      </button>
      {expanded && (
        <div className="border-t border-slate-700/50 p-3">
          <div className="text-xs text-slate-400 mb-1">参数:</div>
          <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all">
            {toolCall.arguments}
          </pre>
          {toolCall.result && (
            <>
              <div className="text-xs text-slate-400 mt-2 mb-1">结果:</div>
              <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-all">
                {toolCall.result}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}
