import { useMemo } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useSystemStore } from '../../stores/system'

// Rough char-to-token estimate when SSE hasn't pushed llm_usage yet
// (e.g. right after page load). ~3.5 chars/token for mixed Chinese/English.
const CHARS_PER_TOKEN = 3.5

type Tier = 'green' | 'amber' | 'red'

function tierFor(pct: number): Tier {
  if (pct >= 80) return 'red'
  if (pct >= 50) return 'amber'
  return 'green'
}

const TIER_CLASS: Record<Tier, { bar: string; text: string }> = {
  green: {
    bar: 'bg-emerald-500',
    text: 'text-emerald-400',
  },
  amber: {
    bar: 'bg-amber-500',
    text: 'text-amber-400',
  },
  red: {
    bar: 'bg-red-500',
    text: 'text-red-400',
  },
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function ContextUsageBar() {
  const sessionId = useSessionStore((s) => s.currentSessionId)
  const tokensUsed = useChatStore((s) => s.tokensUsed.get(sessionId ?? '') ?? 0)
  const messages = useChatStore((s) => s.messages)
  const modelInfo = useSystemStore((s) => s.modelInfo)

  const limit = modelInfo?.context_tokens ?? 0
  const isFallback = modelInfo?.source === 'fallback' || modelInfo?.source === 'bundled'

  // Fallback: estimate tokens from loaded message characters when
  // SSE hasn't pushed llm_usage yet (e.g. after page reload). This
  // avoids the bar showing 0.0% while many messages are actually
  // loaded. Skip tool messages whose content is already folded into
  // the assistant's tool_call parts.
  const estimatedTokens = useMemo(() => {
    let chars = 0
    for (const m of messages.values()) {
      if (m.role === 'tool') continue
      for (const p of m.parts) {
        if (p.type === 'text') chars += p.text.length
        else if (p.type === 'thinking') chars += p.text.length
        else if (p.type === 'tool_call') {
          chars += (p.name?.length ?? 0) + (typeof p.arguments === 'string' ? p.arguments.length : 0)
          const r = p.result
          if (typeof r === 'string') chars += r.length
        }
      }
    }
    return Math.round(chars / CHARS_PER_TOKEN)
  }, [messages])

  const effectiveTokens = tokensUsed > 0 ? tokensUsed : estimatedTokens

  const { pct, tier } = useMemo(() => {
    if (limit <= 0) return { pct: 0, tier: 'green' as Tier }
    const p = Math.min(100, (effectiveTokens / limit) * 100)
    return { pct: p, tier: tierFor(p) }
  }, [effectiveTokens, limit])

  const colors = TIER_CLASS[tier]

  if (limit <= 0) return null

  return (
    <div className="flex items-center gap-2 border-b border-slate-700/50 bg-slate-800/40 px-4 py-1.5 text-[11px]">
      <div className="flex-1">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700/60">
          <div
            className={`h-full transition-all duration-300 ${colors.bar}`}
            style={{ width: `${pct}%` }}
            data-testid="context-progress-bar"
          />
        </div>
      </div>
      <div className={`flex items-center gap-1 font-mono ${colors.text}`}>
        <span data-testid="context-usage-text">
          {formatTokens(effectiveTokens)} / {formatTokens(limit)} ({pct.toFixed(1)}%)
        </span>
        {isFallback && (
          <span
            className="ml-1 inline-flex items-center text-amber-500"
            title={`来自 ${modelInfo?.source ?? 'fallback'} 数据，非最新`}
            data-testid="context-stale-hint"
          >
            <AlertTriangle className="h-3 w-3" />
          </span>
        )}
      </div>
    </div>
  )
}
