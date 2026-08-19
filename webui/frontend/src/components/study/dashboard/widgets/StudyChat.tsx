/**
 * StudyChat — hybrid chat widget for the study detail page.
 *
 * Two modes:
 * 1. Directive Mode (default): read-only agent outputs + send directives
 * 2. Chat Mode: full conversational chat via chat session
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { MessageSquare, Send, Loader2 } from 'lucide-react'
import { api, type StudyRoundAgentOutputsResponse } from '../../../../api/client'
import { useStudyStore } from '../../../../stores/study'
import { useChatStore, type Message, type TextPart } from '../../../../stores/chat'
import { ChatSessionProvider } from '../../../../contexts/ChatSessionContext'
import { MessageList } from '../../../chat/MessageList'
import { Composer } from '../../../chat/Composer'
import { useSSE } from '../../../../hooks/useSSE'
import type { WidgetProps } from '../types'
import { useStudyChatMode, type ChatMode } from './useStudyChatMode'
import { StudyDirectiveComposer } from './StudyDirectiveComposer'

// ── Helpers ──────────────────────────────────────────────────────

function buildMessagesFromOutputs(
  outputs: StudyRoundAgentOutputsResponse['agent_outputs'],
  studyId: string,
  round: number,
): Message[] {
  if (!outputs) return []
  return Object.entries(outputs).map(([agentId, output]): Message => {
    const out = output as Record<string, unknown>
    const text = (out.output as string) || JSON.stringify(out, null, 2)
    const partId = `part:${studyId}:r${round}:${agentId}`
    const textPart: TextPart = { type: 'text', id: partId, text }
    return {
      id: `study:${studyId}:r${round}:${agentId}`,
      session_id: `study:${studyId}:directive`,
      role: 'assistant',
      agent_id: agentId,
      parts: [textPart],
      created_at: Date.now() / 1000,
      metadata: {
        model: agentId,
        ...(out.error ? { error: String(out.error) } : {}),
      },
    }
  })
}

function buildEventMessage(event: { type: string; message: string; timestamp: number }, studyId: string): Message {
  return {
    id: `sse:${event.timestamp}:${event.type}`,
    session_id: `study:${studyId}:directive`,
    role: 'system',
    parts: [{ type: 'text', id: `evt:${event.timestamp}`, text: event.message }],
    created_at: event.timestamp / 1000,
  }
}

// ── Mode Switcher ────────────────────────────────────────────────

function ModeSwitcher({
  mode,
  onModeChange,
  chatCreating,
}: {
  mode: ChatMode
  onModeChange: (m: ChatMode) => void
  chatCreating: boolean
}) {
  return (
    <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-1">
      <button
        onClick={() => onModeChange('directive')}
        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
          mode === 'directive'
            ? 'bg-slate-700 text-slate-200'
            : 'text-slate-500 hover:text-slate-300'
        }`}
      >
        <Send className="h-3 w-3" />
        指令
      </button>
      <button
        onClick={() => onModeChange('chat')}
        disabled={chatCreating}
        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
          mode === 'chat'
            ? 'bg-slate-700 text-slate-200'
            : 'text-slate-500 hover:text-slate-300'
        }`}
      >
        {chatCreating ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <MessageSquare className="h-3 w-3" />
        )}
        对话
      </button>
    </div>
  )
}

// ── Directive Mode ───────────────────────────────────────────────

function DirectiveMode({
  studyId,
  summary,
}: {
  studyId: string
  summary: Record<string, unknown>
}) {
  const currentRound = (summary.current_round as number) ?? 1
  const [selectedRound, setSelectedRound] = useState(currentRound)
  const [loading, setLoading] = useState(false)
  const recentEvents = useStudyStore(s => s.recentEvents)
  const chatStore = useChatStore()
  const eventCountRef = useRef(0)

  // Load agent outputs for selected round
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    api.study
      .roundAgentOutputs(studyId, selectedRound)
      .then((r) => {
        if (!cancelled) {
          const msgs = buildMessagesFromOutputs(r.agent_outputs, studyId, selectedRound)
          chatStore.setMessages([])
          msgs.forEach((m) => chatStore.addMessage(m))
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          chatStore.setMessages([])
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [studyId, selectedRound])

  // Inject SSE events as system messages (only new ones)
  useEffect(() => {
    if (recentEvents.length === 0) return
    // Only add events newer than what we've seen
    const newEvents = recentEvents.slice(0, recentEvents.length - eventCountRef.current)
    eventCountRef.current = recentEvents.length

    newEvents.forEach((event) => {
      chatStore.addMessage(buildEventMessage(event, studyId))
    })
  }, [recentEvents.length])

  return (
    <ChatSessionProvider sessionId={`study:${studyId}:directive`}>
      <div className="flex flex-col" style={{ height: '28rem' }}>
        {/* Round picker */}
        <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
          <span className="text-[10px] text-slate-500">轮次</span>
          <select
            value={selectedRound}
            onChange={(e) => setSelectedRound(Number(e.target.value))}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-300 outline-none"
          >
            {Array.from({ length: currentRound }, (_, i) => i + 1)
              .reverse()
              .map((r) => (
                <option key={r} value={r}>
                  Round {r}
                </option>
              ))}
          </select>
          {loading && (
            <Loader2 className="h-3 w-3 animate-spin text-slate-500" />
          )}
        </div>

        {/* Message list */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <MessageList />
        </div>

        {/* Directive composer */}
        <div className="flex-shrink-0 p-3">
          <StudyDirectiveComposer
            studyId={studyId}
            placeholder="输入研究指令（下轮 researcher 生效）..."
          />
        </div>
      </div>
    </ChatSessionProvider>
  )
}

// ── Chat Mode ────────────────────────────────────────────────────

function ChatModeContent({
  chatSessionId,
}: {
  chatSessionId: string
}) {
  // Activate SSE for this chat session
  useSSE(chatSessionId)

  // Load messages on mount
  const chatStore = useChatStore()
  useEffect(() => {
    chatStore.loadMessages(chatSessionId)
  }, [chatSessionId])

  return (
    <div className="flex flex-col" style={{ height: '28rem' }}>
      {/* Message list */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <MessageList />
      </div>

      {/* Composer */}
      <div className="flex-shrink-0 border-t border-slate-800">
        <Composer />
      </div>
    </div>
  )
}

function ChatMode({
  chatSessionId,
}: {
  chatSessionId: string | null
}) {
  if (!chatSessionId) {
    return (
      <div className="flex h-96 items-center justify-center text-xs text-slate-500">
        正在创建对话会话...
      </div>
    )
  }

  return (
    <ChatSessionProvider sessionId={chatSessionId}>
      <ChatModeContent chatSessionId={chatSessionId} />
    </ChatSessionProvider>
  )
}

// ── Main Widget ──────────────────────────────────────────────────

export function StudyChat({ studyId, summary }: WidgetProps) {
  const {
    mode,
    setMode,
    chatSessionId,
    ensureChatSession,
    creating,
  } = useStudyChatMode(studyId)

  const handleModeChange = useCallback(async (m: ChatMode) => {
    if (m === 'chat') {
      await ensureChatSession()
    }
    setMode(m)
  }, [setMode, ensureChatSession])

  return (
    <div className="space-y-3">
      {/* Mode switcher */}
      <div className="flex items-center justify-between">
        <ModeSwitcher
          mode={mode}
          onModeChange={handleModeChange}
          chatCreating={creating}
        />
        <span className="text-[10px] text-slate-600">
          {mode === 'directive'
            ? '指令模式: 输入方向，下轮生效'
            : '对话模式: 实时与 LLM 交互'}
        </span>
      </div>

      {/* Mode content */}
      {mode === 'directive' ? (
        <DirectiveMode
          studyId={studyId}
          summary={summary as unknown as Record<string, unknown>}
        />
      ) : (
        <ChatMode chatSessionId={chatSessionId} />
      )}
    </div>
  )
}
