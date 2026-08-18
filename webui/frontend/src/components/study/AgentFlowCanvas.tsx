import { useState, useCallback, useEffect } from 'react'
import { api } from '../../api/client'
import { StudyPipeline } from './StudyPipeline'

interface AgentFlowCanvasProps {
  studyId: string
  currentRound: number
  totalRounds?: number
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

interface AgentStatus {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  duration_s?: number
  output_summary?: string
}

export function AgentFlowCanvas({ studyId, currentRound, totalRounds }: AgentFlowCanvasProps) {
  const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})
  const [loading, setLoading] = useState(false)

  const loadAgentStatuses = useCallback(async () => {
    if (!studyId || currentRound <= 0) return
    setLoading(true)
    try {
      const r = await api.study.roundManifest(studyId, currentRound)
      const manifest = r.manifest as Record<string, unknown> | undefined
      const agentOutputs = manifest?.agent_outputs as Record<string, unknown> | undefined

      const statuses: Record<string, AgentStatus> = {}
      let doneCount = 0

      for (const agent of AGENT_SEQUENCE) {
        const agentData = agentOutputs?.[agent.id]
        const isDone = !!agentData
        if (isDone) doneCount++

        let outputSummary = ''
        if (agentData) {
          if (typeof agentData === 'string') {
            outputSummary = agentData.slice(0, 80)
          } else if (typeof agentData === 'object' && agentData !== null) {
            const data = agentData as Record<string, unknown>
            if (data.output) {
              outputSummary = String(data.output).slice(0, 80)
            } else {
              outputSummary = JSON.stringify(data).slice(0, 80)
            }
          }
        }

        statuses[agent.id] = {
          name: agent.id,
          label: agent.label,
          status: isDone ? 'done' : 'pending',
          output_summary: outputSummary,
        }
      }

      // Mark current running agent
      if (doneCount < AGENT_SEQUENCE.length) {
        statuses[AGENT_SEQUENCE[doneCount].id].status = 'running'
      }

      setAgentStatuses(statuses)
    } catch {
      // Manifest may not exist yet
    } finally {
      setLoading(false)
    }
  }, [studyId, currentRound])

  useEffect(() => {
    void loadAgentStatuses()
  }, [loadAgentStatuses])

  return (
    <StudyPipeline
      studyId={studyId}
      currentRound={currentRound}
      totalRounds={totalRounds}
      agentStatuses={agentStatuses}
      loading={loading}
      onRefresh={loadAgentStatuses}
    />
  )
}
