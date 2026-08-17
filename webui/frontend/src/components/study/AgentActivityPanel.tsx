import { useState, useEffect, useCallback } from 'react'
import { Activity, ChevronDown, ChevronRight, CheckCircle, Clock, AlertCircle, Loader } from 'lucide-react'
import { api, type StudyRoundManifestResponse } from '../../api/client'

interface AgentStatus {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  started_at?: string
  completed_at?: string
  duration_s?: number
  output_summary?: string
  hypothesis?: string
  changes?: Record<string, unknown>
}

interface Props {
  studyId: string
  currentRound: number
}

const AGENT_SEQUENCE = [
  { name: 'researcher', label: 'Researcher' },
  { name: 'data_quality', label: 'Data Quality' },
  { name: 'factor_analyst', label: 'Factor Analyst' },
  { name: 'strategist', label: 'Strategist' },
  { name: 'portfolio_construction', label: 'Portfolio' },
  { name: 'risk_controller', label: 'Risk Control' },
  { name: 'attribution_analyst', label: 'Attribution' },
  { name: 'anti_overfit_analyst', label: 'Anti-Overfit' },
]

const STATUS_CONFIG = {
  done: { icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' },
  running: { icon: Loader, color: 'text-sky-400', bg: 'bg-sky-500/10', border: 'border-sky-500/30' },
  error: { icon: AlertCircle, color: 'text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/30' },
  pending: { icon: Clock, color: 'text-slate-500', bg: 'bg-slate-800/40', border: 'border-slate-700' },
}

function AgentRow({ agent, isExpanded, onToggle }: { agent: AgentStatus; isExpanded: boolean; onToggle: () => void }) {
  const config = STATUS_CONFIG[agent.status]
  const Icon = config.icon

  return (
    <div className={`rounded-lg border ${config.border} ${config.bg} transition-all`}>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Icon className={`h-4 w-4 ${config.color} ${agent.status === 'running' ? 'animate-spin' : ''}`} />
        <span className="flex-1 text-xs font-medium text-slate-200">{agent.label}</span>
        {agent.duration_s != null && (
          <span className="font-mono text-[10px] text-slate-500">{agent.duration_s.toFixed(1)}s</span>
        )}
        {agent.status === 'running' && (
          <span className="text-[10px] text-sky-400">运行中...</span>
        )}
        {isExpanded ? (
          <ChevronDown className="h-3 w-3 text-slate-500" />
        ) : (
          <ChevronRight className="h-3 w-3 text-slate-500" />
        )}
      </button>
      
      {isExpanded && agent.output_summary && (
        <div className="border-t border-slate-800/60 px-3 py-2">
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-400">
            {agent.output_summary}
          </pre>
        </div>
      )}
      
      {isExpanded && agent.hypothesis && (
        <div className="border-t border-slate-800/60 px-3 py-2">
          <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">假设</div>
          <p className="mt-1 text-xs text-slate-300">{agent.hypothesis}</p>
        </div>
      )}
      
      {isExpanded && agent.changes && Object.keys(agent.changes).length > 0 && (
        <div className="border-t border-slate-800/60 px-3 py-2">
          <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">变更</div>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-slate-400">
            {JSON.stringify(agent.changes, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

export function AgentActivityPanel({ studyId, currentRound }: Props) {
  const [manifest, setManifest] = useState<StudyRoundManifestResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set())

  const loadManifest = useCallback(async () => {
    if (!studyId || currentRound <= 0) return
    setLoading(true)
    setError('')
    try {
      const r = await api.study.roundManifest(studyId, currentRound)
      setManifest(r)
    } catch (err) {
      // Manifest may not exist yet for in-progress rounds
      setManifest(null)
    } finally {
      setLoading(false)
    }
  }, [studyId, currentRound])

  useEffect(() => {
    void loadManifest()
  }, [loadManifest])

  // Build agent statuses from manifest
  const manifestData = manifest?.manifest as Record<string, unknown> | undefined
  const agentOutputs = manifestData?.agent_outputs as Record<string, unknown> | undefined
  const hypothesisData = manifestData?.hypothesis as { text?: string } | undefined
  const strategyChanges = manifestData?.strategy_changes as Record<string, unknown> | undefined

  const agents: AgentStatus[] = AGENT_SEQUENCE.map((a) => {
    const agentData = agentOutputs?.[a.name] as Record<string, unknown> | undefined
    return {
      name: a.name,
      label: a.label,
      status: agentData ? 'done' : 'pending',
      output_summary: agentData ? formatAgentOutput(agentData) : undefined,
      hypothesis: a.name === 'researcher' ? hypothesisData?.text : undefined,
      changes: a.name === 'strategist' ? strategyChanges : undefined,
    }
  })

  // Determine current running agent (if any)
  const doneCount = agents.filter((a) => a.status === 'done').length
  if (doneCount < agents.length && manifest) {
    agents[doneCount].status = 'running'
  }

  const toggleAgent = (name: string) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const doneSteps = agents.filter((a) => a.status === 'done').length
  const totalSteps = agents.length
  const progress = totalSteps > 0 ? Math.round((doneSteps / totalSteps) * 100) : 0

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 shadow-soft">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <Activity className="h-3 w-3 text-primary-400" />
          Agent 活动 · Round {currentRound}
        </div>
        {loading && (
          <div className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
        )}
      </div>

      {error && (
        <div className="mb-2 rounded-lg border border-rose-800 bg-rose-950/50 p-2 text-[11px] text-rose-300">
          {error}
        </div>
      )}

      {/* Progress bar */}
      <div className="mb-3 flex items-center gap-2 text-[10px] text-slate-500">
        <span className="font-mono text-slate-300">
          {doneSteps}/{totalSteps} 步骤
        </span>
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-700/80">
          <div
            className="h-full bg-gradient-to-r from-sky-500 via-primary-500 to-accent-400 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="font-mono tabular-nums">{progress}%</span>
      </div>

      {/* Agent list */}
      <div className="space-y-1.5">
        {agents.map((agent) => (
          <AgentRow
            key={agent.name}
            agent={agent}
            isExpanded={expandedAgents.has(agent.name)}
            onToggle={() => toggleAgent(agent.name)}
          />
        ))}
      </div>
    </div>
  )
}

function formatAgentOutput(output: unknown): string {
  if (typeof output === 'string') return output
  if (typeof output === 'object' && output !== null) {
    try {
      return JSON.stringify(output, null, 2)
    } catch {
      return String(output)
    }
  }
  return String(output)
}
