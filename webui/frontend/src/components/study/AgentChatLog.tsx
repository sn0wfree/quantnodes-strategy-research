import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { Send } from 'lucide-react'
import { api, type MessagePartLike } from '../../api/client'
import { useChatStore } from '../../stores/chat'
import type { Message } from '../../stores/chat'
import { ChatSessionProvider } from '../../contexts/ChatSessionContext'
import { MessageList } from '../chat/MessageList'
import { RoundPicker } from './RoundPicker'

interface AgentChatLogProps {
  studyId: string
  selectedRound: number
  onSelectedRoundChange: (round: number) => void
  totalRounds?: number
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

function roundSessionId(studyId: string, round: number): string {
  return `study:${studyId}:r:${round}`
}

export function AgentChatLog({
  studyId,
  selectedRound,
  onSelectedRoundChange,
  totalRounds,
}: AgentChatLogProps) {
  const sessionId = roundSessionId(studyId, selectedRound)
  return (
    <ChatSessionProvider sessionId={sessionId}>
      <AgentChatLogInner
        studyId={studyId}
        selectedRound={selectedRound}
        onSelectedRoundChange={onSelectedRoundChange}
        totalRounds={totalRounds}
      />
    </ChatSessionProvider>
  )
}

function AgentChatLogInner({
  studyId,
  selectedRound,
  onSelectedRoundChange,
  totalRounds,
}: AgentChatLogProps) {
  const messages = useChatStore((s) => s.messages)
  const addMessage = useChatStore((s) => s.addMessage)
  const removeMessage = useChatStore((s) => s.removeMessage)

  // Load agent outputs → synthetic Message[] for the selected round.
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const r = await api.study.roundAgentOutputs(studyId, selectedRound)
        if (cancelled) return
        const synth = buildMessages(
          r.agent_outputs ?? {},
          studyId,
          selectedRound,
        )
        for (const m of synth) {
          addMessage(m)
        }
      } catch {
        /* empty round is fine */
      }
    }
    void run()
    return () => {
      cancelled = true
      // Best-effort cleanup of synthetic ids for this round.
      const ids = synthIdsForRound(studyId, selectedRound)
      for (const id of ids) {
        if (messages.has(id)) removeMessage(id)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studyId, selectedRound])

  const isEmpty = useMemo(() => {
    let hasAny = false
    for (const [, m] of messages) {
      if (m.session_id === roundSessionId(studyId, selectedRound)) {
        hasAny = true
        break
      }
    }
    return !hasAny
  }, [messages, studyId, selectedRound])

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-shrink-0 items-center justify-between rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-slate-500">
          Agent 群聊 · Round {selectedRound}
        </span>
        <RoundPicker
          currentRound={selectedRound}
          totalRounds={totalRounds}
          onChange={onSelectedRoundChange}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-900/40">
        {isEmpty ? (
          <div className="flex h-full items-center justify-center px-4 py-10 text-center text-xs text-slate-600">
            {selectedRound < 1
              ? '请选择一轮查看群聊记录'
              : `Round ${selectedRound} 暂无 Agent 输出`}
          </div>
        ) : (
          <MessageList />
        )}
      </div>

      <StudyDirectiveComposer studyId={studyId} />
    </div>
  )
}

// ── lightweight composer (study-directive only; no chat server call) ──

function StudyDirectiveComposer({ studyId }: { studyId: string }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [hint, setHint] = useState<string>('')
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  const submit = useCallback(async () => {
    const value = text.trim()
    if (!value || sending) return
    setSending(true)
    setHint('')
    try {
      await api.study.directive(studyId, value, 'webui')
      setText('')
      setHint('已提交，下轮生效')
      window.setTimeout(() => setHint(''), 2500)
    } catch (err) {
      setHint('提交失败：' + ((err as Error).message || 'unknown'))
    } finally {
      setSending(false)
      taRef.current?.focus()
    }
  }, [text, sending, studyId])

  return (
    <div className="flex-shrink-0 rounded-xl border border-slate-800 bg-slate-900/60 p-2">
      <div className="flex items-end gap-2">
        <textarea
          ref={taRef}
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              void submit()
            }
          }}
          placeholder="例：改成动量因子 + 减小 top_n（Cmd/Ctrl+Enter 提交）"
          className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-shadow placeholder:text-slate-600 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/40"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={sending || !text.trim()}
          className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white transition-all hover:bg-indigo-500 active:scale-95 disabled:opacity-50"
        >
          <Send className="h-3.5 w-3.5" />
          {sending ? '提交中…' : '提交指令'}
        </button>
      </div>
      {hint && (
        <p className="mt-1 text-[10px] text-slate-500">{hint}</p>
      )}
    </div>
  )
}

// ── helpers helpers

function buildMessages(
  agentOutputs: Record<string, Record<string, unknown> | string | null>,
  studyId: string,
  round: number,
): Message[] {
  const out: Message[] = []
  const entries = Object.entries(agentOutputs)
  entries.sort(([aId, aOut], [bId, bOut]) => {
    const aT = parseTimestamp(
      aOut && typeof aOut === 'object' ? aOut.timestamp : null,
    )
    const bT = parseTimestamp(
      bOut && typeof bOut === 'object' ? bOut.timestamp : null,
    )
    if (aT !== bT) return aT - bT
    return aId.localeCompare(bId)
  })
  for (const [agentId, raw] of entries) {
    if (!raw) continue
    const obj: Record<string, unknown> = typeof raw === 'string' ? { output: raw } : raw
    out.push(agentOutputToMessage(agentId, obj, studyId, round))
  }
  return out
}

function agentOutputToMessage(
  agentId: string,
  output: Record<string, unknown>,
  studyId: string,
  round: number,
): Message {
  const parts = buildParts(output)
  const error = typeof output.error === 'string' ? output.error : null
  return {
    id: messageId(agentId, round),
    session_id: roundSessionId(studyId, round),
    role: 'assistant',
    agent_id: agentId,
    parts,
    created_at: parseTimestamp(output.timestamp),
    metadata: {
      status: error ? 'error' : 'completed',
      error: error ?? undefined,
    },
  }
}

function messageId(agentId: string, round: number): string {
  return `study-msg:${agentId}:r${round}`
}

function synthIdsForRound(studyId: string, round: number): string[] {
  const out: string[] = []
  for (const agentId of Object.keys(AGENT_LABEL)) {
    out.push(`study-msg:${agentId}:r${round}`)
  }
  void studyId
  return out
}

function buildParts(output: Record<string, unknown>): Message['parts'] {
  const rawParts = output.parts
  if (Array.isArray(rawParts) && rawParts.length > 0) {
    const parts: Message['parts'] = []
    for (const p of rawParts as MessagePartLike[]) {
      const coerced = coercePart(p)
      if (coerced) parts.push(coerced)
    }
    if (parts.length > 0) return parts
  }
  const text =
    (typeof output.output === 'string' && output.output) ||
    JSON.stringify(output, null, 2)
  return [{ type: 'text', text } as Message['parts'][number]]
}

function coercePart(p: MessagePartLike): Message['parts'][number] | null {
  if (!p || typeof p !== 'object' || typeof p.type !== 'string') return null
  const known = ['text', 'tool_call', 'thinking', 'file_edit', 'table', 'chart', 'image', 'html', 'agent']
  if (!known.includes(p.type)) return null
  return p as unknown as Message['parts'][number]
}

function parseTimestamp(value: unknown): number {
  if (typeof value === 'string') {
    const t = new Date(value).getTime()
    return Number.isFinite(t) ? t : Date.now()
  }
  if (typeof value === 'number') return value
  return Date.now()
}

// (end of file)