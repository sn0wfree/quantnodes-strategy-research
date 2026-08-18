import { useState } from 'react'
import { GitBranch } from 'lucide-react'
import { StudyAgentNode } from './StudyAgentNode'
import { AgentNodeDetail } from './AgentNodeDetail'
import { DAGProgressBar } from '../workflow/DAGProgressBar'

interface AgentStatus {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  duration_s?: number
  output_summary?: string
}

interface StudyPipelineProps {
  studyId: string
  currentRound: number
  totalRounds?: number
  agentStatuses: Record<string, AgentStatus>
  loading?: boolean
  onRefresh?: () => void
}

const AGENT_SEQUENCE = [
  { id: 'researcher', label: 'Researcher', abbr: 'R' },
  { id: 'data_quality', label: 'Data Quality', abbr: 'DQ' },
  { id: 'factor_analyst', label: 'Factor Analyst', abbr: 'FA' },
  { id: 'strategist', label: 'Strategist', abbr: 'ST' },
  { id: 'portfolio_construction', label: 'Portfolio', abbr: 'PC' },
  { id: 'risk_controller', label: 'Risk Control', abbr: 'RC' },
  { id: 'attribution_analyst', label: 'Attribution', abbr: 'AA' },
  { id: 'anti_overfit_analyst', label: 'Anti-Overfit', abbr: 'AO' },
]

const NODES_PER_ROW = 4

function chunk<T>(array: T[], size: number): T[][] {
  const chunks: T[][] = []
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size))
  }
  return chunks
}

export function StudyPipeline({
  studyId,
  currentRound,
  totalRounds,
  agentStatuses,
  loading = false,
}: StudyPipelineProps) {
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null)

  const rows = chunk(AGENT_SEQUENCE, NODES_PER_ROW)
  const doneCount = Object.values(agentStatuses).filter((s) => s.status === 'done').length
  const progress = Math.round((doneCount / AGENT_SEQUENCE.length) * 100)

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 shadow-soft overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
          <GitBranch className="h-3 w-3 text-primary-400" />
          Agent 流水线 · Round {currentRound}
          {loading && (
            <div className="ml-2 h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-primary-500" />
          )}
        </div>
        {totalRounds != null && (
          <span className="font-mono text-[9px] text-slate-600">共 {totalRounds} 轮</span>
        )}
      </div>

      {/* Pipeline */}
      <div className="p-4">
        {rows.map((row, rowIndex) => (
          <div key={rowIndex}>
            {/* Row content */}
            <div className="flex items-center justify-center gap-1">
              {row.map((agent, colIndex) => {
                const status = agentStatuses[agent.id]
                return (
                  <div key={agent.id} className="flex items-center">
                    <StudyAgentNode
                      agent={agent}
                      status={status}
                      onClick={() => setSelectedAgent(agent.id)}
                    />
                    {/* Horizontal connector (not on last node of row) */}
                    {colIndex < row.length - 1 && (
                      <div className="flex items-center mx-1">
                        <div className="h-0.5 w-4 bg-slate-700" />
                        <div className="h-0 w-0 border-t-[3px] border-b-[3px] border-l-[5px] border-transparent border-l-slate-700" />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Vertical connector between rows */}
            {rowIndex < rows.length - 1 && (
              <div className="flex justify-center my-2">
                <div className="flex flex-col items-center">
                  <div className="h-3 w-0.5 bg-slate-700" />
                  <div className="h-0 w-0 border-l-[3px] border-r-[3px] border-t-[5px] border-transparent border-t-slate-700" />
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Progress bar */}
      <div className="border-t border-slate-800 px-4 py-2">
        <DAGProgressBar
          progress={progress}
          completed={doneCount}
          total={AGENT_SEQUENCE.length}
        />
      </div>

      {/* Legend */}
      <div className="border-t border-slate-800 px-4 py-2 flex items-center gap-4 text-[9px] text-slate-500">
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-emerald-500" /> 完成</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-sky-500 animate-pulse" /> 运行中</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-slate-700" /> 待执行</span>
        <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-rose-500" /> 失败</span>
      </div>

      {/* Detail modal */}
      {selectedAgent && (
        <AgentNodeDetail
          agentId={selectedAgent}
          studyId={studyId}
          currentRound={currentRound}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </div>
  )
}
