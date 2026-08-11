import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Loader2, MessageSquareText, Minus, Send, Sparkles, Square } from 'lucide-react'
import { api } from '../../api/client'
import { sanitizeSpec, type DagSpec } from './dagSpec'

interface OrchToolCall {
  id: string
  name: string
  args: string
  result?: string
  status: 'running' | 'done' | 'error'
}

interface OrchMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  toolCalls: OrchToolCall[]
  streaming?: boolean
}

interface OrchestratorChatProps {
  dagId: string
  /** Read the current canvas as a DAG spec (attached to each user message). */
  getSnapshot: () => DagSpec
  /** Apply a validated spec to the canvas (replace + auto-layout + history). */
  onApplyDag: (spec: DagSpec) => void
}

const EXAMPLES = [
  '把「研究动量因子有效性」拆解为 DAG',
  '帮我规划一条从数据检查到回测的工作流',
  '当前流程缺少评估环节，补上',
]

function snapshotMarkdown(spec: DagSpec): string {
  return `\n\n\`\`\`json\n${JSON.stringify(spec)}\n\`\`\``
}

/** Parse a submit_dag_step tool result into { applied, errors?, spec }. */
function parseToolResult(raw: string | unknown): { applied: boolean; errors?: string[]; spec?: DagSpec } {
  try {
    const obj = typeof raw === 'string' ? JSON.parse(raw) : (raw as Record<string, unknown>)
    return { applied: obj.applied === true, errors: obj.errors as string[] | undefined }
  } catch {
    return { applied: false, errors: ['工具返回无法解析'] }
  }
}

export function OrchestratorChat({ dagId, getSnapshot, onApplyDag }: OrchestratorChatProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<OrchMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [initError, setInitError] = useState<string | null>(null)
  const esRef = useRef<EventSource | null>(null)
  const busyRef = useRef(false)
  const listRef = useRef<HTMLDivElement | null>(null)

  const scrollToBottom = () => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }

  const patchMessage = useCallback((id: string, fn: (m: OrchMessage) => OrchMessage) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? fn(m) : m)))
  }, [])

  const patchToolCall = useCallback(
    (messageId: string, toolId: string, fn: (t: OrchToolCall) => OrchToolCall) => {
      patchMessage(messageId, (m) => ({
        ...m,
        toolCalls: m.toolCalls.map((t) => (t.id === toolId ? fn(t) : t)),
      }))
    },
    [patchMessage],
  )

  // ── session bootstrap: get/create dag:{dagId}, load history ──────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await api.orchestrate.session(dagId)
        if (cancelled) return
        setSessionId(r.session_id)
        const hist = await api.get<{ messages: Array<{
          id: string; role: string; parts: Array<Record<string, unknown>>
        }> }>(`/chat/session/${encodeURIComponent(r.session_id)}/messages?limit=200`)
        if (cancelled) return
        const restored: OrchMessage[] = (hist.messages ?? []).map((m) => {
          const text = (m.parts ?? [])
            .filter((p) => p.type === 'text')
            .map((p) => String(p.text ?? ''))
            .join('')
          const toolCalls: OrchToolCall[] = (m.parts ?? [])
            .filter((p) => p.type === 'tool_call')
            .map((p) => ({
              id: String(p.id),
              name: String(p.name ?? ''),
              args: typeof p.arguments === 'string' ? p.arguments : JSON.stringify(p.arguments ?? {}),
              result: typeof p.result === 'string' ? p.result : JSON.stringify(p.result ?? {}),
              status: p.status === 'error' ? 'error' : 'done',
            }))
          return { id: m.id, role: m.role === 'user' ? 'user' : 'assistant', text, toolCalls }
        })
        setMessages(restored)
        scrollToBottom()
      } catch {
        if (!cancelled) setInitError('编排会话初始化失败，请刷新重试')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [dagId])

  // ── SSE subscription (native EventSource + reconnect) ────────────
  useEffect(() => {
    if (!sessionId) return
    let parsedToken = ''
    try {
      const raw = localStorage.getItem('sr-auth')
      parsedToken = raw ? JSON.parse(raw).state?.token ?? '' : ''
    } catch {}
    const params = new URLSearchParams({ session_id: sessionId })
    if (parsedToken) params.set('token', parsedToken)
    const es = new EventSource(`/api/chat/events?${params}`)
    esRef.current = es

    const applyFromTool = (messageId: string, toolId: string) => {
      patchMessage(messageId, (m) => {
        const call = m.toolCalls.find((t) => t.id === toolId)
        if (!call) return m
        const { applied } = parseToolResult(call.result)
        if (!applied || !call.args) return m
        try {
          const spec = sanitizeSpec(JSON.parse(call.args).dag as DagSpec)
          onApplyDag(spec)
          call.result = JSON.stringify({ applied: true, diff: `合并应用：${spec.nodes.length} 节点 / ${spec.edges.length} 连线` })
          return { ...m, toolCalls: [...m.toolCalls] }
        } catch {
          return m
        }
      })
    }

    es.onmessage = (ev) => {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(ev.data)
      } catch {
        return
      }
      const messageId = String(data.message_id ?? '')
      const type = String(ev.type || '')
      if (type === 'message_received') {
        scrollToBottom()
        return
      }
      if (type === 'text_delta') {
        const delta = String(data.delta ?? '')
        patchMessage(messageId, (m) => ({
          ...m, text: m.text + delta, streaming: true,
        }))
        scrollToBottom()
        return
      }
      if (type === 'text.started') {
        patchMessage(messageId, (m) => ({ ...m, text: String(data.delta ?? ''), streaming: true }))
        return
      }
      if (type === 'text.ended') {
        const final = String(data.text ?? '')
        patchMessage(messageId, (m) => ({ ...m, text: final, streaming: false }))
        return
      }
      if (type === 'assistant_message') {
        const content = String(data.content ?? data.text ?? '')
        patchMessage(messageId, (m) => ({ ...m, text: content, streaming: false }))
        return
      }
      if (type === 'tool_call') {
        const toolId = String(data.id ?? '')
        const name = String(data.name ?? '')
        const args = typeof data.arguments === 'string' ? data.arguments : JSON.stringify(data.arguments ?? {})
        patchMessage(messageId, (m) => ({
          ...m,
          toolCalls: [...m.toolCalls, { id: toolId, name, args, status: 'running' as const }],
        }))
        return
      }
      if (type === 'tool_result') {
        const toolId = String(data.id ?? '')
        const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result ?? {})
        patchToolCall(messageId, toolId, (t) => ({ ...t, result, status: 'done' }))
        applyFromTool(messageId, toolId)
        return
      }
      if (type === 'agent_done') {
        setBusy(false)
        busyRef.current = false
      }
    }
    es.onerror = () => {
      // native reconnect
    }
    return () => {
      es.close()
      esRef.current = null
    }
  }, [sessionId, patchMessage, patchToolCall, onApplyDag])

  // ── send ─────────────────────────────────────────────────────────
  const send = async () => {
    const content = input.trim()
    if (!content || !sessionId || busyRef.current) return
    const userMsg: OrchMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      text: content,
      toolCalls: [],
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setBusy(true)
    busyRef.current = true
    const full = content + snapshotMarkdown(getSnapshot())
    try {
      const r = await api.sendOrchestrate(sessionId, full)
      // placeholder assistant message (streaming text may arrive without
      // a prior text.started only in replay edge cases)
      setMessages((prev) => {
        if (prev.some((m) => m.id === r.message_id)) return prev
        return [...prev, { id: r.message_id, role: 'assistant', text: '', toolCalls: [], streaming: true }]
      })
    } catch {
      setBusy(false)
      busyRef.current = false
    }
    scrollToBottom()
  }

  const cancel = async () => {
    if (!sessionId) return
    try {
      await api.post('/chat/cancel', { session_id: sessionId })
    } catch {}
    setBusy(false)
    busyRef.current = false
  }

  // ── collapsed rail ───────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className="wf-panel-solid flex w-10 shrink-0 flex-col items-center border-r py-2">
        <button
          onClick={() => setCollapsed(false)}
          title="展开编排助手"
          className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <MessageSquareText className="h-4 w-4" />
        </button>
        <span className="mt-1 [writing-mode:vertical-rl] text-[10px] text-slate-500">编排助手</span>
      </aside>
    )
  }

  return (
    <aside className="wf-panel-solid flex w-72 shrink-0 flex-col border-r">
      {/* header */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Sparkles className="h-4 w-4 text-primary-400" />
        <span className="wf-text-main text-xs font-semibold">编排助手</span>
        <span className="wf-text-sub truncate font-mono text-[10px]">{dagId}</span>
        <button
          onClick={() => setCollapsed(true)}
          title="折叠"
          className="ml-auto rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* messages */}
      <div ref={listRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {initError && (
          <div className="rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-[11px] text-rose-300">{initError}</div>
        )}
        {messages.length === 0 && !initError && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 rounded-lg border border-slate-700/60 bg-slate-900 px-3 py-2.5">
              <Bot className="h-4 w-4 shrink-0 text-primary-400" />
              <p className="wf-text-sub text-[11px] leading-relaxed">
                描述你的任务，我会把它逐步拆解为 DAG：每轮只改一处，通过工具提交并自动应用到画布，直到完成。
              </p>
            </div>
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => setInput(ex)}
                className="wf-text-sub block w-full rounded-lg border border-slate-700/40 bg-slate-900/60 px-3 py-2 text-left text-[11px] transition-colors hover:border-primary-500/50 hover:text-slate-200"
              >
                {ex}
              </button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="space-y-1.5">
            {m.role === 'user' ? (
              <div className="ml-6 rounded-lg bg-primary-600/90 px-3 py-2 text-[12px] text-white">
                {m.text.split('\n```json')[0]}
              </div>
            ) : (
              <div className="space-y-1.5">
                {m.toolCalls.length === 0 && (
                  <div className="wf-text-main rounded-lg border border-slate-700/50 bg-slate-900/70 px-3 py-2 text-[12px] leading-relaxed">
                    {m.text}
                  </div>
                )}
                {m.toolCalls.map((t) => {
                  let parsed: { applied: boolean; errors?: string[]; diff?: string } | null = null
                  if (t.result) {
                    try {
                      parsed = JSON.parse(t.result)
                    } catch {}
                  }
                  return (
                    <div key={t.id} className="rounded-lg border border-slate-700/50 bg-slate-900/70 px-3 py-2">
                      <div className="flex items-center gap-1.5">
                        {t.status === 'running' ? (
                          <Loader2 className="h-3 w-3 animate-spin text-amber-400" />
                        ) : (
                          <span className={`h-2 w-2 rounded-full ${parsed?.applied ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                        )}
                        <span className="font-mono text-[10px] text-slate-300">{t.name}</span>
                      </div>
                      {parsed ? (
                        <div className="mt-1 text-[11px] leading-relaxed">
                          {parsed.applied ? (
                            <span className="text-emerald-300">
                              已应用 ✓ {parsed.diff ? `· ${parsed.diff}` : ''}
                            </span>
                          ) : (
                            <span className="block text-rose-300">
                              校验未通过，已回传助手修正：
                              <span className="mt-0.5 block space-y-0.5">
                                {(parsed.errors ?? []).map((e, i) => (
                                  <span key={i} className="block">· {e}</span>
                                ))}
                              </span>
                            </span>
                          )}
                        </div>
                      ) : (
                        <div className="mt-1 text-[11px] text-slate-400">{t.status === 'running' ? '提交中…' : '处理中…'}</div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* input */}
      <div className="border-t p-2.5">
        <div className="flex items-end gap-1.5">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
            rows={2}
            placeholder={busy ? '助手编排中…' : '输入任务或指令（Enter 发送）'}
            disabled={busy}
            className="wf-input min-h-[52px] flex-1 resize-none rounded-lg border px-2.5 py-2 text-xs outline-none focus:border-primary-500 disabled:opacity-50"
          />
          {busy ? (
            <button
              onClick={cancel}
              title="停止"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-rose-600 text-white hover:bg-rose-500"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              onClick={() => void send()}
              disabled={!input.trim() || !sessionId}
              title="发送"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-40"
            >
              <Send className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <p className="wf-text-sub mt-1.5 text-[10px] leading-relaxed">
          每轮只改一处 · 校验失败自动修正重试 · 修改自动应用到画布（可 Ctrl+Z 撤销）
        </p>
      </div>
    </aside>
  )
}
