import { useState, useEffect } from 'react'
import { X, Clock, CheckCircle, Loader2 } from 'lucide-react'
import { api } from '../../api/client'

interface AgentNodeDetailProps {
  agentId: string
  studyId: string
  currentRound: number
  onClose: () => void
}

interface AgentDetail {
  label: string
  status: string
  duration_s?: number
  hypothesis?: string
  changes?: Record<string, unknown>
  output?: string
}

interface ChatEntry {
  role: string
  content: string
}

const AGENT_LABELS: Record<string, string> = {
  researcher: 'Researcher',
  data_quality: 'Data Quality',
  factor_analyst: 'Factor Analyst',
  strategist: 'Strategist',
  portfolio_construction: 'Portfolio Construction',
  risk_controller: 'Risk Controller',
  attribution_analyst: 'Attribution Analyst',
  anti_overfit_analyst: 'Anti-Overfit Analyst',
}

export function AgentNodeDetail({ agentId, studyId, currentRound, onClose }: AgentNodeDetailProps) {
  const [agentData, setAgentData] = useState<AgentDetail | null>(null)
  const [chatLogs, setChatLogs] = useState<ChatEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const loadData = async () => {
      setLoading(true)
      try {
        const r = await api.study.roundAgentOutputs(studyId, currentRound)
        if (cancelled) return
        const agentOutputs = r.agent_outputs ?? {}

        const agentOutput = agentOutputs[agentId]

        let outputSummary = ''
        if (agentOutput) {
          if (typeof agentOutput === 'string') {
            outputSummary = agentOutput
          } else if (agentOutput.output) {
            outputSummary = String(agentOutput.output)
          } else {
            outputSummary = JSON.stringify(agentOutput, null, 2)
          }
        }

        setAgentData({
          label: AGENT_LABELS[agentId] ?? agentId,
          status: agentOutput ? 'completed' : 'pending',
          output: outputSummary,
        })

        // Parse chat logs from output
        if (outputSummary) {
          setChatLogs([{ role: 'assistant', content: outputSummary }])
        }
      } catch {
        setAgentData({
          label: AGENT_LABELS[agentId] ?? agentId,
          status: 'pending',
        })
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadData()
    return () => { cancelled = true }
  }, [agentId, studyId, currentRound])

  const StatusIcon = agentData?.status === 'completed' ? CheckCircle
    : agentData?.status === 'running' ? Loader2
    : Clock

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <StatusIcon className={`h-4 w-4 ${
              agentData?.status === 'completed' ? 'text-emerald-400' :
              agentData?.status === 'running' ? 'text-sky-400 animate-spin' :
              'text-slate-500'
            }`} />
            <h3 className="text-sm font-semibold text-slate-200">{agentData?.label ?? agentId}</h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Content */}
        <div className="max-h-[60vh] overflow-y-auto p-4 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
            </div>
          ) : (
            <>
              {/* Status info */}
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                  <span className="text-slate-500">状态</span>
                  <span className={`ml-2 font-medium ${
                    agentData?.status === 'completed' ? 'text-emerald-400' :
                    agentData?.status === 'running' ? 'text-sky-400' :
                    'text-slate-400'
                  }`}>
                    {agentData?.status === 'completed' ? '已完成' :
                     agentData?.status === 'running' ? '运行中' : '等待中'}
                  </span>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                  <span className="text-slate-500">轮次</span>
                  <span className="ml-2 font-medium text-slate-300">Round {currentRound}</span>
                </div>
              </div>

              {/* Hypothesis */}
              {agentData?.hypothesis && (
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">假设</div>
                  <p className="mt-1 text-xs text-slate-300 leading-relaxed">{agentData.hypothesis}</p>
                </div>
              )}

              {/* Changes */}
              {agentData?.changes && Object.keys(agentData.changes).length > 0 && (
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">变更</div>
                  <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950/60 p-2 font-mono text-[11px] text-slate-400">
                    {JSON.stringify(agentData.changes, null, 2)}
                  </pre>
                </div>
              )}

              {/* Chat logs */}
              {chatLogs.length > 0 && (
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-wider text-slate-500">输出</div>
                  <div className="mt-2 space-y-2">
                    {chatLogs.map((log, i) => (
                      <div key={i} className="rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                        <div className="text-[10px] text-slate-500">{log.role}</div>
                        <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] text-slate-400">
                          {log.content}
                        </pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Empty state */}
              {!agentData?.hypothesis && !agentData?.changes && chatLogs.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-500">
                  暂无详细信息
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
