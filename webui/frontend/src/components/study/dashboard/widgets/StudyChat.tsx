/**
 * StudyChat — unified chat widget for the study detail page.
 *
 * Layout:
 *   Header: [Plan | Build] mode switcher + current round + panel toggle
 *   Body (flex row):
 *     Main column: MessageList (with round separators) + InterruptApprovalCard + StudyChatComposer
 *     Right panel: KeyPointsPanel (all rounds, collapsible)
 *
 * Both modes show the same message stream.
 * Plan mode defaults composer to 指令, Build mode defaults to 对话.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { Loader2, PanelRightOpen, PanelRightClose } from 'lucide-react'
import { api, type StudyRoundAgentOutputsResponse } from '../../../../api/client'
import { useStudyStore } from '../../../../stores/study'
import { useChatStore, type Message, type TextPart } from '../../../../stores/chat'
import { ChatSessionProvider } from '../../../../contexts/ChatSessionContext'
import { MessageList } from '../../../chat/MessageList'
import type { WidgetProps } from '../types'
import { useStudyChatMode } from './useStudyChatMode'
import { StudyChatComposer } from './StudyChatComposer'
import { KeyPointsPanel } from './KeyPointsPanel'
import { InterruptApprovalCard } from './InterruptApprovalCard'

// ── Helpers ──────────────────────────────────────────────────────

function extractAgentText(out: Record<string, unknown>): string {
  const o = out.output as Record<string, unknown> | undefined
  if (!o) return JSON.stringify(out, null, 2)
  if (typeof o === 'string') return o

  const agent = out.agent as string | undefined
  const lines: string[] = []

  switch (agent) {
    case 'researcher':
    case 'strategist': {
      if (o.action) lines.push(`**行动**: ${o.action}`)
      if (o.hypothesis) lines.push(`**假设**: ${o.hypothesis}`)
      if (o.reason) lines.push(`**理由**: ${o.reason}`)
      if (o.factor_direction) lines.push(`**方向**: ${o.factor_direction}`)
      if (o.expected_impact) lines.push(`**预期影响**: ${o.expected_impact}`)
      if (o.change_plan && typeof o.change_plan === 'object') {
        lines.push(`**变更计划**: ${JSON.stringify(o.change_plan)}`)
      }
      break
    }
    case 'data_quality': {
      const passed = o.passed ? '✅ 通过' : '❌ 未通过'
      lines.push(`**数据质量**: ${passed}`)
      const warnings = (o.warnings as string[]) ?? []
      if (warnings.length > 0) {
        lines.push(`**告警** (${warnings.length}):`)
        warnings.forEach((w, i) => lines.push(`  ${i + 1}. ${w}`))
      }
      break
    }
    case 'factor_analyst': {
      if (o.recommendation) lines.push(`**建议**: ${o.recommendation}`)
      const flags = (o.risk_flags as string[]) ?? []
      if (flags.length > 0) {
        lines.push('**风险标记**:')
        flags.forEach((f, i) => lines.push(`  ${i + 1}. ${f}`))
      }
      break
    }
    case 'portfolio_construction': {
      if (o.method) lines.push(`**方法**: ${o.method}`)
      if (o.portfolio_vol) lines.push(`**组合波动率**: ${o.portfolio_vol}`)
      if (o.recommendation && typeof o.recommendation === 'object') {
        const rec = o.recommendation as Record<string, unknown>
        if (rec.ready_for_backtest !== undefined) {
          lines.push(`**回测就绪**: ${rec.ready_for_backtest ? '✅' : '❌'}`)
        }
        if (rec.next_step) lines.push(`**下一步**: ${rec.next_step}`)
      }
      if (o.proposed_change && typeof o.proposed_change === 'object') {
        lines.push(`**变更方案**: ${JSON.stringify(o.proposed_change)}`)
      }
      break
    }
    case 'risk_controller':
    case 'attribution_analyst': {
      if (o.error) {
        lines.push(`**状态**: ❌ ${o.error}`)
        if (o.hint) lines.push(`**提示**: ${o.hint}`)
      } else {
        if (o.risk_passed !== undefined) {
          lines.push(`**风控**: ${o.risk_passed ? '✅ 通过' : '❌ 未通过'} (${o.risk_rating ?? '-'})`)
        }
        if (o.alpha !== undefined) lines.push(`**Alpha**: ${o.alpha}`)
        if (o.beta_mkt !== undefined) lines.push(`**Beta**: ${o.beta_mkt}`)
      }
      break
    }
    case 'anti_overfit_analyst': {
      if (o.verdict) lines.push(`**结论**: ${o.verdict}`)
      if (o.weighted_score !== undefined) lines.push(`**加权分**: ${o.weighted_score}`)
      if (o.analysis) lines.push(o.analysis as string)
      const suggestions = (o.suggestions as string[]) ?? []
      if (suggestions.length > 0) {
        lines.push('**建议**:')
        suggestions.forEach((s, i) => lines.push(`  ${i + 1}. ${s}`))
      }
      break
    }
    case 'backtest_diagnostics': {
      if (o.severity) lines.push(`**严重度**: ${o.severity}`)
      if (o.root_cause) lines.push(`**根因**: ${o.root_cause}`)
      if (o.fix_suggestion) lines.push(`**修复建议**: ${o.fix_suggestion}`)
      break
    }
    default: {
      return (o.analysis as string)
        || (o.error as string)
        || JSON.stringify(o, null, 2)
    }
  }

  return lines.length > 0
    ? lines.join('\n')
    : JSON.stringify(o, null, 2)
}

function buildMessagesFromOutputs(
  outputs: StudyRoundAgentOutputsResponse['agent_outputs'],
  studyId: string,
  round: number,
): Message[] {
  if (!outputs) return []
  return Object.entries(outputs).map(([agentId, output]): Message => {
    const out = output as Record<string, unknown>
    const text = extractAgentText(out)
    const partId = `part:${studyId}:r${round}:${agentId}`
    const textPart: TextPart = { type: 'text', id: partId, text }
    return {
      id: `study:${studyId}:r${round}:${agentId}`,
      session_id: `study:${studyId}:stream`,
      role: 'assistant',
      agent_id: agentId,
      parts: [textPart],
      created_at: Date.now() / 1000,
      metadata: {
        model: agentId,
        kind: 'agent',
        round,
        ...(out.error ? { error: String(out.error) } : {}),
      },
    }
  })
}

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

/** separatorKey function: extracts round from metadata for round dividers. */
function roundKey(msg: Message): string | null {
  const round = msg.metadata?.round
  if (round != null) return String(round)
  return null
}

// ── Panel toggle button ──────────────────────────────────────────

function PanelToggle({
  open,
  onClick,
}: {
  open: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
      title={open ? '收起右栏' : '展开右栏'}
    >
      {open ? (
        <PanelRightClose className="h-4 w-4" />
      ) : (
        <PanelRightOpen className="h-4 w-4" />
      )}
    </button>
  )
}

// ── Main Widget ──────────────────────────────────────────────────

export function StudyChat({ studyId, summary }: WidgetProps) {
  const {
    mode,
    setMode,
  } = useStudyChatMode(studyId)

  const currentRound = (summary?.current_round as number) ?? 1
  const [selectedRound, setSelectedRound] = useState(currentRound)
  const [loading, setLoading] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [pendingInterrupt, setPendingInterrupt] = useState<{
    interruptId: string
    hypothesis?: string
    message?: string
  } | null>(null)
  const recentEvents = useStudyStore((s) => s.recentEvents)
  const chatStore = useChatStore()
  const eventCountRef = useRef(0)
  const prevStudyIdRef = useRef<string | null>(null)

  // Sync selectedRound with currentRound when it changes
  useEffect(() => {
    if (currentRound > 0) {
      setSelectedRound(currentRound)
    }
  }, [currentRound])

  // Load agent outputs for selected round
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    api.study
      .roundAgentOutputs(studyId, selectedRound)
      .then((r) => {
        if (!cancelled) {
          const msgs = buildMessagesFromOutputs(r.agent_outputs, studyId, selectedRound)
          // Replace agent messages in stream (keep chat/directive messages)
          const existing = Array.from(chatStore.messages.values())
          const nonAgent = existing.filter(
            (m) => m.metadata?.kind !== 'agent',
          )
          chatStore.setMessages([])
          nonAgent.forEach((m) => chatStore.addMessage(m))
          msgs.forEach((m) => chatStore.addMessage(m))
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          // Remove agent messages on error
          const existing = Array.from(chatStore.messages.values())
          const nonAgent = existing.filter(
            (m) => m.metadata?.kind !== 'agent',
          )
          chatStore.setMessages([])
          nonAgent.forEach((m) => chatStore.addMessage(m))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [studyId, selectedRound])

  // Inject SSE events as system messages
  useEffect(() => {
    if (recentEvents.length === 0) return
    const newEvents = recentEvents.slice(eventCountRef.current)
    eventCountRef.current = recentEvents.length

    newEvents.forEach((event) => {
      chatStore.addMessage(buildEventMessage(event, studyId, selectedRound))

      // Detect HITL interrupt from SSE events
      // Backend emits study_paused with reason="hitl_approval"
      if ((event as any).type === 'study_paused' && (event as any).reason === 'hitl_approval') {
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

  const handleSelectRound = useCallback((round: number) => {
    setSelectedRound(round)
  }, [])

  const handlePanelToggle = useCallback(() => {
    setPanelOpen((prev) => {
      const next = !prev
      try {
        localStorage.setItem(`sr-study-chat-panel-${studyId}`, String(next))
      } catch { /* ignore */ }
      return next
    })
  }, [studyId])

  // Restore panel state from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem(`sr-study-chat-panel-${studyId}`)
      if (saved !== null) setPanelOpen(saved !== 'false')
    } catch { /* ignore */ }
  }, [studyId])

  return (
    <ChatSessionProvider sessionId={`study:${studyId}:stream`}>
      <div className="flex h-full flex-col">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
          <div className="flex items-center gap-3">
            {/* Mode switcher */}
            <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-1">
              <button
                onClick={() => setMode('plan')}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === 'plan'
                    ? 'bg-slate-700 text-slate-200'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                📋 Plan
              </button>
              <button
                onClick={() => setMode('build')}
                className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                  mode === 'build'
                    ? 'bg-slate-700 text-slate-200'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                🔧 Build
              </button>
            </div>

            {/* Current round indicator */}
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span>
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

          <PanelToggle open={panelOpen} onClick={handlePanelToggle} />
        </div>

        {/* Body: main column + right panel */}
        <div className="flex min-h-0 flex-1">
          {/* Main column */}
          <div className="flex min-h-0 flex-1 flex-col">
            {/* Message stream */}
            <div className="min-h-0 flex-1">
              <MessageList separatorKey={roundKey} />
            </div>

            {/* HITL Approval Card (shown when interrupt is pending) */}
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
              <StudyChatComposer
                studyId={studyId}
                mode={mode}
              />
            </div>
          </div>

          {/* Right panel */}
          {panelOpen && (
            <div className="w-64 flex-shrink-0 border-l border-slate-800">
              <KeyPointsPanel
                studyId={studyId}
                selectedRound={selectedRound}
                onSelectRound={handleSelectRound}
                refreshKey={recentEvents.length}
              />
            </div>
          )}
        </div>
      </div>
    </ChatSessionProvider>
  )
}
