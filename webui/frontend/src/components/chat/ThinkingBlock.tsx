import { useState, useEffect } from 'react'
import { Sparkles, ChevronRight, Copy, Check } from 'lucide-react'

interface ThinkingBlockProps {
  text: string
  collapsed?: boolean
  /** Whether the thinking is still streaming (shows shimmer dot). */
  streaming?: boolean
  /** Wall-clock ms when thinking started; used to compute "Thought for Xs". */
  startTime?: number
  /** Wall-clock ms when thinking ended; if omitted, computed from streaming. */
  endTime?: number
}

/** Format a millisecond duration as a short human-readable string. */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`
}

export function ThinkingBlock({
  text,
  collapsed = true,
  streaming = false,
  startTime,
  endTime,
}: ThinkingBlockProps) {
  const [isExpanded, setIsExpanded] = useState(!collapsed)
  const [copied, setCopied] = useState(false)
  const [tickMs, setTickMs] = useState(0)

  // While streaming, periodically refresh the elapsed counter so the label
  // grows live (e.g. "Thinking for 2.3s" → "Thinking for 2.4s").
  useEffect(() => {
    if (!streaming || !startTime) return
    const id = setInterval(() => setTickMs(Date.now() - startTime), 100)
    return () => clearInterval(id)
  }, [streaming, startTime])

  if (!text) return null

  // Label: "Thinking for Xs" while streaming, "Thought for Xs" once done.
  let label: string
  if (streaming) {
    const elapsed =
      startTime !== undefined ? tickMs || Date.now() - startTime : 0
    label =
      elapsed > 0
        ? `Thinking for ${formatDuration(elapsed)}`
        : 'Thinking…'
  } else if (startTime !== undefined && endTime !== undefined) {
    label = `Thought for ${formatDuration(endTime - startTime)}`
  } else {
    label = 'Thought'
  }

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="my-2 border-l-2 border-violet-500/40 bg-violet-950/10 rounded-r-md overflow-hidden">
      {/* Single-line header (always visible). role="button" instead of <button>
          so we can legally nest the copy icon-button inside. */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setIsExpanded(!isExpanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setIsExpanded((v) => !v)
          }
        }}
        className="flex w-full cursor-pointer items-center gap-1.5 px-2 py-1 text-left text-[11px] text-violet-300/80 hover:bg-violet-950/20 transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-violet-400 transition-transform ${
            isExpanded ? 'rotate-90' : ''
          }`}
        />
        <Sparkles className="h-3 w-3 text-violet-400" />
        <span className="font-medium">{label}</span>
        {streaming && (
          <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-pulse ml-0.5" />
        )}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-violet-400/60">
          {text.length > 50 && <span>{text.length} 字</span>}
          <button
            type="button"
            onClick={handleCopy}
            className="cursor-pointer hover:text-violet-300"
            title="复制"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </span>
      </div>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-violet-500/20 px-3 py-2 text-[11px] text-violet-200/80 whitespace-pre-wrap max-h-72 overflow-y-auto font-mono leading-relaxed">
          {text}
        </div>
      )}
    </div>
  )
}