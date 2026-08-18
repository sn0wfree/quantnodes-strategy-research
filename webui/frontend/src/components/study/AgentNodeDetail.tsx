import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api, type MessagePartLike } from '../../api/client'
import { AssistantMessage } from '../chat/AssistantMessage'
import type { Message } from '../../stores/chat'

interface AgentNodeDetailProps {
  agentId: string
  studyId: string
  currentRound: number
  onClose: () => void
}

const AGENT_LABEL: Record<string, string> = {
  researcher: 'Researcher',
  data_quality: 'Data Quality',
  factor_analyst: 'Factor Analyst',
  strategist: 'Strategist',
  portfolio_construction: 'Portfolio',
  risk_controller: 'Risk Control',
  attribution_analyst: 'Attribution',
  anti_overfit_analyst: 'Anti-Overfit',
  explore: 'Explore',
  backtest: 'Backtest',
}

export function AgentNodeDetail({
  agentId,
  studyId,
  currentRound,
  onClose,
}: AgentNodeDetailProps) {
  const [rawOutput, setRawOutput] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let aborted = false
    const run = async () => {
      setLoading(true)
      setError('')
      try {
        const r = await api.study.roundAgentOutputs(studyId, currentRound)
        if (aborted) return
        const agentOutput = r.agent_outputs?.[agentId] ?? null
        setRawOutput((agentOutput as Record<string, unknown> | null) ?? null)
      } catch (err) {
        if (!aborted) setError((err as Error).message)
      } finally {
        if (!aborted) setLoading(false)
      }
    }
    void run()
    return () => {
      aborted = true
    }
  }, [agentId, studyId, currentRound])

  const syntheticMessage = useMemo<Message | null>(() => {
    if (!rawOutput) return null
    const parts = buildParts(rawOutput)
    return {
      id: `agent-${agentId}-r${currentRound}`,
      session_id: `study:${studyId}:r:${currentRound}`,
      role: 'assistant',
      agent_id: agentId,
      parts,
      created_at: parseTimestamp(rawOutput.timestamp),
      metadata: {
        agent_id: agentId,
        status: 'completed',
        duration_ms:
          typeof rawOutput.duration_ms === 'number' ? rawOutput.duration_ms : undefined,
      },
    }
  }, [rawOutput, agentId, currentRound, studyId])

  const label = AGENT_LABEL[agentId] ?? agentId
  const hasError = !!rawOutput && !!rawOutput.error
  const isPending = !rawOutput && !loading && !error

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${label} 详情`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-slate-700 bg-slate-900 shadow-elevated"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-100">{label}</span>
            <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 font-mono text-[10px] text-slate-400">
              Round {currentRound}
            </span>
            {loading && (
              <span className="text-[10px] text-slate-500">加载中…</span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {error && (
            <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-xs text-rose-300">
              {error}
            </div>
          )}
          {isPending && (
            <div className="rounded-lg border border-dashed border-slate-800 px-4 py-10 text-center text-xs text-slate-600">
              该节点在 Round {currentRound} 暂无输出。
            </div>
          )}
          {hasError && (
            <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-xs text-rose-300">
              <p className="font-semibold">agent 返回错误</p>
              <pre className="mt-1 whitespace-pre-wrap text-[11px]">
                {String(rawOutput!.error)}
              </pre>
            </div>
          )}
          {syntheticMessage && !hasError && (
            <AssistantMessage message={syntheticMessage} readOnly layout="flat" />
          )}
        </div>
      </div>
    </div>
  )
}

// ─ helpers helpers

function parseTimestamp(value: unknown): number {
  if (typeof value === 'string') {
    const t = new Date(value).getTime()
    return Number.isFinite(t) ? t : Date.now()
  }
  if (typeof value === 'number') return value
  return Date.now()
}

function buildParts(output: Record<string, unknown>): Message['parts'] {
  // Prefer structured parts when present.
  const rawParts = output.parts
  if (Array.isArray(rawParts) && rawParts.length > 0) {
    const parts: Message['parts'] = []
    for (const p of rawParts as MessagePartLike[]) {
      const coerced = coercePart(p)
      if (coerced) parts.push(coerced)
    }
    if (parts.length > 0) return parts
  }

  // Fallback: wrap the legacy ``output`` field as a single text part.
  const text =
    (typeof output.output === 'string' && output.output) ||
    JSON.stringify(output, null, 2)
  return [
    {
      type: 'text',
      text,
    } as Message['parts'][number],
  ]
}

function coercePart(p: MessagePartLike): Message['parts'][number] | null {
  if (!p || typeof p !== 'object' || typeof p.type !== 'string') return null
  // The chat AssistantMessage knows how to render these part types.
  // We pass them through verbatim; anything unknown becomes a text part.
  const known = ['text', 'tool_call', 'thinking', 'file_edit', 'table', 'chart', 'image', 'html', 'agent']
  if (!known.includes(p.type)) return null
  return p as unknown as Message['parts'][number]
}