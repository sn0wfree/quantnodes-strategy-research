/**
 * StudyChat — unified chat widget for the study detail page.
 *
 * Layout:
 *   Header: current round indicator + (回到最新) link
 *   Body: MessageList (with round separators + scroll-to-bottom arrow)
 *         + RoundNavRail (right-edge overlay dots for quick round switching)
 *         + InterruptApprovalCard (when HITL pending)
 *         + StudyChatComposer (directive-only)
 */
import { useState, useEffect, useRef } from 'react'
import { Loader2 } from 'lucide-react'
import {
  api,
  type StudyRoundAgentOutputsResponse,
  type StudyRoundSummary,
} from '../../../../api/client'
import { useStudyStore } from '../../../../stores/study'
import { useChatStore, type Message } from '../../../../stores/chat'
import { ChatSessionProvider } from '../../../../contexts/ChatSessionContext'
import { MessageList } from '../../../chat/MessageList'
import type { WidgetProps } from '../types'
import { StudyChatComposer } from './StudyChatComposer'
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

    // Stage 3: If history exists, build multi-part message from execution trace
    const history = out.history as Array<{ type: string; data: Record<string, any>; ts: number }> | undefined
    let parts: Message['parts'] = []

    if (history && history.length > 0) {
      // Convert history events to message parts
      for (const evt of history) {
        const evtType = evt.type as string
        const d = evt.data || {}

        if (evtType.startsWith('thinking')) {
          // Skip empty thinking_delta events
          if (evtType === 'thinking_delta' && !d.delta) continue
          // Create a collapsed thinking part for start/done
          if (evtType === 'thinking_start' || evtType === 'thinking_done') {
            parts.push({
              type: 'thinking',
              id: `think:${agentId}:${evt.ts}`,
              text: '',
              collapsed: true,
            } as any)
          } else if (evtType === 'thinking_delta') {
            // Accumulate thinking text into existing thinking part
            const lastThinking = parts.findLast(p => p.type === 'thinking' && !(p as any).collapsed)
            if (lastThinking) {
              ;(lastThinking as any).text = ((lastThinking as any).text || '') + (d.delta || '')
            }
          }
        } else if (evtType === 'tool_call') {
          parts.push({
            type: 'tool_call',
            id: d.id || `tc:${agentId}:${evt.ts}`,
            name: d.tool || d.name || 'unknown',
            arguments: d.arguments || d.args || {},
            status: 'running',
          } as any)
        } else if (evtType === 'tool_result') {
          // Find the matching tool_call and update its status
          const tcId = d.id || d.tool_call_id
          const tcPart = parts.findLast(p => p.type === 'tool_call' && (p as any).id === tcId) as any
          if (tcPart) {
            tcPart.status = d.status || 'completed'
            tcPart.result = d.result || d.output || ''
          } else {
            // No matching tool_call found — add as text
            parts.push({
              type: 'text',
              id: `tr:${agentId}:${evt.ts}`,
              text: `📋 ${d.tool || ''}: ${JSON.stringify(d.result || d.output || '').slice(0, 200)}`,
            })
          }
        } else if (evtType === 'text_delta' || evtType === 'text_started' || evtType === 'text_ended') {
          // Accumulate text into existing text part or create new
          const lastText = parts.findLast(p => p.type === 'text') as any
          if (lastText && evtType === 'text_delta') {
            lastText.text = (lastText.text || '') + (d.text || d.delta || '')
          } else if (evtType === 'text_delta' || evtType === 'text_started') {
            parts.push({
              type: 'text',
              id: `txt:${agentId}:${evt.ts}`,
              text: d.text || d.delta || '',
            })
          }
        } else if (evtType === 'assistant_message') {
          const content = d.content || d.text || ''
          if (content) {
            parts.push({
              type: 'text',
              id: `msg:${agentId}:${evt.ts}`,
              text: typeof content === 'string' ? content : JSON.stringify(content),
            })
          }
        } else if (evtType === 'loop_end') {
          // Skip loop_end — the final answer is already in the output
        }
      }
    }

    // Always include the final formatted output as a text part
    const text = extractAgentText(out)
    if (text) {
      parts.push({
        type: 'text',
        id: `part:${studyId}:r${round}:${agentId}`,
        text: text,
      })
    }

    // Fallback: if no parts were created, use single text part
    if (parts.length === 0) {
      parts = [{ type: 'text', id: `part:${studyId}:r${round}:${agentId}`, text }]
    }

    return {
      id: `study:${studyId}:r${round}:${agentId}`,
      session_id: `study:${studyId}:stream`,
      role: 'assistant',
      agent_id: agentId,
      parts,
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

// ── Agent-level SSE event → chat message ──────────────────────

function buildAgentEventMessage(
  event: any,
  studyId: string,
  currentRound: number,
): Message {
  const eventType = event.type as string
  const agentId = event.agent || event.data?.agent || ''
  const data = event.data || {}
  const ts = event.timestamp || Date.now()

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
    text = '🧠 正在思考...'
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
    // Generic fallback
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

// ── RoundNavRail (right-edge overlay progress dots) ──────────────

function RoundNavRail({
  rounds,
  selectedRound,
  onSelectRound,
}: {
  rounds: StudyRoundSummary[]
  selectedRound: number
  onSelectRound: (n: number) => void
}) {
  // Sort R1 (top) → R{N} (bottom)
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
                {/* Tooltip on hover */}
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

// ── Main Widget ──────────────────────────────────────────────────

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

  // Load agent outputs for selected round
  // When study is completed, load history for full execution trace
  useEffect(() => {
    let cancelled = false
    setLoading(true)

    const studyStatus = summary?.execution_status
    const shouldLoadHistory = studyStatus !== 'running'

    api.study
      .roundAgentOutputs(studyId, selectedRound, {
        include_history: shouldLoadHistory,
        history_limit: 200,
      })
      .then((r) => {
        if (!cancelled) {
          const msgs = buildMessagesFromOutputs(r.agent_outputs, studyId, selectedRound)
          // Merge with existing messages — don't clear SSE events
          const existing = Array.from(chatStore.messages.values())
          const existingAgentIds = new Set(
            existing
              .filter((m) => m.agent_id)
              .map((m) => m.agent_id),
          )
          // Only add API response messages for agents that don't already have messages
          const newMsgs = msgs.filter((m) => !m.agent_id || !existingAgentIds.has(m.agent_id))
          newMsgs.forEach((m) => chatStore.addMessage(m))
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          // On error, just stop loading — keep existing SSE messages
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [studyId, selectedRound])

  // Inject SSE events as system messages + agent-level events
  useEffect(() => {
    if (recentEvents.length === 0) return
    const newEvents = recentEvents.slice(eventCountRef.current)
    eventCountRef.current = recentEvents.length

    newEvents.forEach((event) => {
      const eventType = (event as any).type as string

      // Agent-level events: forwarded from AgentLoop via on_event adapter
      // These have type like "agent_thinking_start", "agent_tool_call", etc.
      if (eventType?.startsWith('agent_')) {
        chatStore.addMessage(buildAgentEventMessage(event, studyId, selectedRound))
        return
      }

      // Study-level events (phase, complete, etc.)
      chatStore.addMessage(buildEventMessage(event, studyId, selectedRound))

      // Detect HITL interrupt from SSE events
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

  // scrollKey: change when switching rounds so MessageList scrolls to top
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
          {/* Message stream with round nav rail overlay */}
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
            <StudyChatComposer studyId={studyId} />
          </div>
        </div>
      </div>
    </ChatSessionProvider>
  )
}