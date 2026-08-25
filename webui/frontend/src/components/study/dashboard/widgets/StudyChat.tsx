/**
 * StudyChat — unified chat widget for the study detail page.
 *
 * Uses the chat session API for loading messages (event-sourced via Projector).
 * Each round creates a session: study:{studyId}:round:{roundNum}
 *
 * Layout:
 *   Header: round indicator
 *   Body: MessageList (with round separators + scroll-to-bottom arrow)
 *         + RoundNavRail (right-edge overlay dots)
 *         + InterruptApprovalCard (when HITL pending)
 *         + StudyChatComposer (directive-only)
 */
import { useState, useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import {
  type StudyRoundSummary,
} from '../../../../api/client'
import { useStudyStore } from '../../../../stores/study'
import { useChatStore, type Message } from '../../../../stores/chat'
import { ChatSessionProvider } from '../../../../contexts/ChatSessionContext'
import { MessageList } from '../../../chat/MessageList'
import type { WidgetProps } from '../types'
import { StudyChatComposer } from './StudyChatComposer'
import { InterruptApprovalCard } from './InterruptApprovalCard'
import { getAgentStyle } from '../../agentStyles'

// ── SSE event → Message (for live streaming) ──────────────────

function buildEventMessage(
  event: { type: string; message: string; timestamp: number },
  studyId: string,
  currentRound: number,
): Message {
  return {
    id: `sse:${event.timestamp}:${event.type}`,
    session_id: `study:${studyId}:stream`,
    role: 'system',
    parts: [{ type: 'text', id: `evt:${event.timestamp}`, text: event.message }],
    created_at: event.timestamp / 1000,
    metadata: {
      kind: 'system',
      round: currentRound,
    },
  }
}

function buildAgentEventMessage(
  event: any,
  studyId: string,
  currentRound: number,
): Message {
  const eventType = event.type as string
  const agentId = event.agent || event.data?.agent || ''
  const data = event.data || {}
  const ts = event.timestamp || Date.now()
  const agentStyle = getAgentStyle(agentId)

  let text = ''

  if (eventType === 'agent_tool_call') {
    const toolName = data.tool || data.name || '未知工具'
    const args = data.arguments || data.args || {}
    const argsStr = typeof args === 'string' ? args : JSON.stringify(args, null, 2)
    text = `🔧 \`${toolName}\`\n\`\`\`json\n${argsStr}\n\`\`\``
  } else if (eventType === 'agent_tool_result') {
    const toolName = data.tool || data.name || ''
    const status = data.status || 'ok'
    const result = data.result || data.output || ''
    const resultStr = typeof result === 'string' ? result.slice(0, 500) : JSON.stringify(result, null, 2).slice(0, 500)
    text = `📋 \`${toolName}\` → ${status}\n\`\`\`\n${resultStr}\n\`\`\``
  } else if (eventType === 'agent_thinking_start') {
    text = `${agentStyle.icon} 正在思考...`
  } else if (eventType === 'agent_thinking_delta') {
    text = data.delta || data.text || ''
  } else if (eventType === 'agent_text_delta') {
    text = data.text || data.delta || ''
  } else if (eventType === 'agent_assistant_message') {
    const content = data.content || data.text || ''
    text = typeof content === 'string' ? content : JSON.stringify(content)
  } else if (eventType === 'agent_loop_end') {
    const reason = data.reason || data.finished_reason || ''
    text = `✅ 完成 (${reason})`
  } else {
    const message = data.message || data.text || ''
    text = message || `${eventType.replace('agent_', '')}`
  }

  if (!text) return { id: `skip:${ts}:${eventType}`, session_id: `study:${studyId}:stream`, role: 'system', parts: [], created_at: ts / 1000, metadata: { kind: 'system', round: currentRound } }

  return {
    id: `agent:${ts}:${eventType}:${agentId}`,
    session_id: `study:${studyId}:stream`,
    role: 'system',
    agent_id: agentId || undefined,
    parts: [{ type: 'text', id: `aevt:${ts}:${eventType}`, text }],
    created_at: ts / 1000,
    metadata: {
      kind: 'agent',
      model: agentId,
      round: currentRound,
    },
  }
}

/** separatorKey function: extracts round from metadata for round dividers. */
function roundKey(msg: Message): string | null {
  const round = msg.metadata?.round
  if (round != null) return String(round)
  return null
}

// ── RoundNavRail ──────────────────────────────────────────────

function RoundNavRail({
  rounds,
  selectedRound,
  onSelectRound,
}: {
  rounds: StudyRoundSummary[]
  selectedRound: number
  onSelectRound: (n: number) => void
}) {
  const sorted = [...rounds].sort((a, b) => a.round_num - b.round_num)

  return (
    <div
      className="pointer-events-none absolute inset-y-0 right-1 z-10 flex items-center"
      aria-label="轮次导航"
    >
      <div className="pointer-events-auto flex flex-col items-center gap-0 rounded-full bg-slate-900/40 px-1 py-2 backdrop-blur-sm">
        {sorted.map((r, i) => {
          const isActive = r.round_num === selectedRound
          return (
            <div key={r.round_num} className="flex flex-col items-center">
              <button
                type="button"
                onClick={() => onSelectRound(r.round_num)}
                title={`Round ${r.round_num} · ${r.verdict ?? '—'}`}
                aria-label={`跳转到 Round ${r.round_num}`}
                className={`group relative rounded-full transition-all duration-200 ${
                  isActive
                    ? 'h-2.5 w-2.5 bg-primary-400 shadow-[0_0_8px_rgba(99,179,237,0.5)]'
                    : 'h-1.5 w-1.5 bg-slate-600 hover:bg-slate-400'
                }`}
              >
                <div className="pointer-events-none absolute right-full top-1/2 mr-2 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-slate-700 bg-slate-800/95 px-2 py-1 text-[10px] text-slate-200 shadow-lg backdrop-blur group-hover:block">
                  <div className="font-medium text-slate-100">Round {r.round_num}</div>
                  <div className="text-slate-400">
                    {r.verdict ?? '—'} · {r.run_name.slice(0, 24)}
                  </div>
                </div>
              </button>
              {i < sorted.length - 1 && (
                <div className="my-0.5 h-3 w-px bg-slate-700" />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Main Widget ────────────────────────────────────────────────

export function StudyChat({ studyId, summary }: WidgetProps) {
  const currentRound = (summary?.current_round as number) ?? 1
  const [selectedRound, setSelectedRound] = useState(currentRound)
  const [loading, setLoading] = useState(false)
  const [pendingInterrupt, setPendingInterrupt] = useState<{
    interruptId: string
    hypothesis?: string
    message?: string
  } | null>(null)
  const recentEvents = useStudyStore((s) => s.recentEvents)
  const chatStore = useChatStore()
  const eventCountRef = useRef(0)
  const prevStudyIdRef = useRef<string | null>(null)

  const rounds: StudyRoundSummary[] = summary?.recent_rounds ?? []

  // Sync selectedRound with currentRound when it changes
  useEffect(() => {
    if (currentRound > 0) {
      setSelectedRound(currentRound)
    }
  }, [currentRound])

  // Load messages for selected round from chat session API (event-sourced)
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    const sessionId = `study:${studyId}:round:${selectedRound}`

    chatStore.loadMessages(sessionId)
      .then(() => {
        if (!cancelled) setLoading(false)
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [studyId, selectedRound])

  // Inject SSE events as system messages + agent-level events (live streaming)
  useEffect(() => {
    if (recentEvents.length === 0) return
    const newEvents = recentEvents.slice(eventCountRef.current)
    eventCountRef.current = recentEvents.length

    newEvents.forEach((event) => {
      const eventType = (event as any).type as string

      if (eventType?.startsWith('agent_')) {
        chatStore.addMessage(buildAgentEventMessage(event, studyId, selectedRound))
        return
      }

      chatStore.addMessage(buildEventMessage(event, studyId, selectedRound))

      if (eventType === 'study_paused' && (event as any).reason === 'hitl_approval') {
        setPendingInterrupt({
          interruptId: `pending:${studyId}:${selectedRound}`,
          hypothesis: (event as any).hypothesis,
          message: (event as any).message || '等待审批...',
        })
      }
    })
  }, [recentEvents.length])

  // Clear messages on studyId change
  useEffect(() => {
    if (prevStudyIdRef.current !== studyId) {
      chatStore.setMessages([])
      eventCountRef.current = 0
      prevStudyIdRef.current = studyId
    }
  }, [studyId])

  const scrollKey = `${studyId}:${selectedRound}`

  return (
    <ChatSessionProvider sessionId={`study:${studyId}:stream`}>
      <div className="flex h-full flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-slate-800 px-3 py-2">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="font-mono text-slate-300">
              Round {selectedRound}
              {loading && <Loader2 className="ml-1 inline h-3 w-3 animate-spin" />}
            </span>
            {selectedRound !== currentRound && (
              <button
                onClick={() => setSelectedRound(currentRound)}
                className="text-[10px] text-primary-400 hover:text-primary-300"
              >
                (回到最新)
              </button>
            )}
          </div>
        </div>

        {/* Body */}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <MessageList separatorKey={roundKey} scrollKey={scrollKey} />
            {rounds.length > 1 && (
              <RoundNavRail
                rounds={rounds}
                selectedRound={selectedRound}
                onSelectRound={setSelectedRound}
              />
            )}
          </div>

          {/* HITL Approval Card */}
          {pendingInterrupt && (
            <div className="flex-shrink-0 border-t border-slate-800 p-3">
              <InterruptApprovalCard
                studyId={studyId}
                interruptId={pendingInterrupt.interruptId}
                hypothesis={pendingInterrupt.hypothesis}
                message={pendingInterrupt.message}
                onApproved={() => setPendingInterrupt(null)}
                onRejected={() => setPendingInterrupt(null)}
              />
            </div>
          )}

          {/* Composer */}
          <div className="flex-shrink-0">
            <StudyChatComposer studyId={studyId} />
          </div>
        </div>
      </div>
    </ChatSessionProvider>
  )
}
