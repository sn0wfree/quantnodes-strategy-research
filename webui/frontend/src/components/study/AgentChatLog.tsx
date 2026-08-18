import { useState, useEffect } from 'react'
import { MessageSquare, Filter, ChevronDown, ChevronRight, Bot, User } from 'lucide-react'
import { api, type StudyRoundAgentOutputsResponse } from '../../api/client'

interface ChatEntry {
  agent: string
  role: 'system' | 'user' | 'assistant'
  content: string
  timestamp?: string
  tokens?: { prompt: number; completion: number }
}

interface Props {
  studyId: string
  currentRound: number
  agents?: string[]
}

const DEFAULT_AGENTS = [
  'researcher',
  'data_quality',
  'factor_analyst',
  'strategist',
  'portfolio_construction',
  'risk_controller',
  'attribution_analyst',
  'anti_overfit_analyst',
]

const AGENT_LABELS: Record<string, string> = {
  researcher: 'Researcher',
  data_quality: 'Data Quality',
  factor_analyst: 'Factor Analyst',
  strategist: 'Strategist',
  portfolio_construction: 'Portfolio',
  risk_controller: 'Risk Control',
  attribution_analyst: 'Attribution',
  anti_overfit_analyst: 'Anti-Overfit',
}

function ChatBubble({ entry }: { entry: ChatEntry }) {
  const isSystem = entry.role === 'system'
  const isUser = entry.role === 'user'

  return (
    <div className={`rounded-lg border p-3 ${
      isSystem
        ? 'border-slate-700 bg-slate-800/40'
        : isUser
          ? 'border-primary-500/30 bg-primary-500/5'
          : 'border-emerald-500/30 bg-emerald-500/5'
    }`}>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {isUser ? (
            <User className="h-3 w-3 text-primary-400" />
          ) : (
            <Bot className="h-3 w-3 text-emerald-400" />
          )}
          <span className="text-[10px] font-medium text-slate-400">
            {AGENT_LABELS[entry.agent] ?? entry.agent}
          </span>
          <span className={`rounded-full px-1.5 py-0.5 text-[8px] font-medium ${
            isSystem
              ? 'bg-slate-700 text-slate-400'
              : isUser
                ? 'bg-primary-500/20 text-primary-300'
                : 'bg-emerald-500/20 text-emerald-300'
          }`}>
            {entry.role}
          </span>
        </div>
        {entry.timestamp && (
          <span className="font-mono text-[9px] text-slate-600">{entry.timestamp}</span>
        )}
      </div>
      <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-300">
        {entry.content}
      </pre>
      {entry.tokens && (
        <div className="mt-2 border-t border-slate-800/60 pt-2 text-[9px] text-slate-500">
          tokens: {entry.tokens.prompt + entry.tokens.completion} (prompt: {entry.tokens.prompt}, completion: {entry.tokens.completion})
        </div>
      )}
    </div>
  )
}

export function AgentChatLog({ studyId, currentRound, agents }: Props) {
  const [agentOutputs, setAgentOutputs] = useState<StudyRoundAgentOutputsResponse['agent_outputs']>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedAgent, setSelectedAgent] = useState<string>('all')
  const [expandedEntries, setExpandedEntries] = useState<Set<number>>(new Set())

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!studyId || currentRound <= 0) return
      setLoading(true)
      setError('')
      try {
        const r = await api.study.roundAgentOutputs(studyId, currentRound)
        if (!cancelled) setAgentOutputs(r.agent_outputs ?? {})
      } catch {
        if (!cancelled) setAgentOutputs({})
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [studyId, currentRound])

  // Build chat entries from agent outputs
  const chatEntries: ChatEntry[] = []

  for (const [agentName, data] of Object.entries(agentOutputs)) {
    if (selectedAgent !== 'all' && selectedAgent !== agentName) continue

    // data may be the full agent JSON {output, input, duration_ms, ...}
    // or a plain string for older formats
    let content: string
    if (typeof data === 'string') {
      content = data
    } else if (data && typeof data === 'object') {
      content = data.output ?? JSON.stringify(data, null, 2)
    } else {
      content = String(data)
    }

    chatEntries.push({
      agent: agentName,
      role: 'assistant',
      content,
      tokens: data?.tokens as ChatEntry['tokens'] | undefined,
    })
  }

  const availableAgents = agents ?? DEFAULT_AGENTS
  const toggleEntry = (index: number) => {
    setExpandedEntries((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <MessageSquare className="h-3 w-3 text-primary-400" />
          Agent 聊天记录 · Round {currentRound}
        </div>
        {loading && (
          <div className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
        )}
      </div>

      {/* Agent filter */}
      <div className="mb-3 flex items-center gap-2">
        <Filter className="h-3 w-3 text-slate-500" />
        <select
          value={selectedAgent}
          onChange={(e) => setSelectedAgent(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] text-slate-300 outline-none focus:border-primary-500"
        >
          <option value="all">全部 Agent</option>
          {availableAgents.map((a) => (
            <option key={a} value={a}>{AGENT_LABELS[a] ?? a}</option>
          ))}
        </select>
        <span className="text-[10px] text-slate-500">{chatEntries.length} 条记录</span>
      </div>

      {error && (
        <div className="mb-2 rounded-lg border border-rose-800 bg-rose-950/50 p-2 text-[11px] text-rose-300">
          {error}
        </div>
      )}

      {/* Chat entries */}
      {chatEntries.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-600">
          {loading ? '加载中...' : '暂无聊天记录'}
        </div>
      ) : (
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {chatEntries.map((entry, i) => (
            <div key={i}>
              <button
                type="button"
                onClick={() => toggleEntry(i)}
                className="flex w-full items-center gap-1 text-left"
              >
                {expandedEntries.has(i) ? (
                  <ChevronDown className="h-3 w-3 text-slate-500" />
                ) : (
                  <ChevronRight className="h-3 w-3 text-slate-500" />
                )}
                <span className="text-[10px] text-slate-400">
                  {AGENT_LABELS[entry.agent] ?? entry.agent}
                </span>
                <span className={`rounded-full px-1 py-0.5 text-[8px] ${
                  entry.role === 'assistant'
                    ? 'bg-emerald-500/20 text-emerald-300'
                    : 'bg-slate-700 text-slate-400'
                }`}>
                  {entry.role}
                </span>
              </button>
              {expandedEntries.has(i) && (
                <div className="mt-1">
                  <ChatBubble entry={entry} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
