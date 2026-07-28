import { useState } from 'react'
import { Brain, ChevronDown, ChevronRight } from 'lucide-react'

interface ThinkingBlockProps {
  text: string
  collapsed?: boolean
}

export function ThinkingBlock({ text, collapsed = true }: ThinkingBlockProps) {
  const [isExpanded, setIsExpanded] = useState(!collapsed)

  if (!text) return null

  return (
    <div className="my-1.5 rounded-lg border border-violet-500/20 bg-violet-950/20 overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-violet-950/30 transition-colors"
      >
        {isExpanded ? (
          <ChevronDown className="h-3 w-3 text-violet-400" />
        ) : (
          <ChevronRight className="h-3 w-3 text-violet-400" />
        )}
        <Brain className="h-3.5 w-3.5 text-violet-400" />
        <span className="text-violet-300">推理过程</span>
        <span className="text-violet-500 text-[10px]">
          {text.length > 100 ? `${Math.ceil(text.length / 4)} tokens` : ''}
        </span>
      </button>
      {isExpanded && (
        <div className="border-t border-violet-500/10 px-3 py-2 text-xs text-violet-300/70 whitespace-pre-wrap max-h-60 overflow-y-auto">
          {text}
        </div>
      )}
    </div>
  )
}
