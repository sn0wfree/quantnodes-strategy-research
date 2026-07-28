import { create } from 'zustand'

export type AgentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'aborted'

export interface IterationToolCall {
  id: string
  name: string
  status: 'running' | 'done' | 'error'
}

export interface IterationDetail {
  iteration: number
  thought?: string
  tool_calls: IterationToolCall[]
  timestamp: number
}

export interface Agent {
  id: string
  session_id: string
  status: AgentStatus
  name: string
  description?: string
  created_at: number
  updated_at: number
  finished_reason?: 'success' | 'failure' | 'abort' | 'max_iterations'
  tool_calls_count: number
  compaction_count: number
  context_tokens: number
  context_tokens_limit: number
  iterations_detail: IterationDetail[]
  color?: string
}

interface AgentState {
  agents: Map<string, Agent>
  setAgents: (agents: Agent[]) => void
  updateAgent: (id: string, updater: (agent: Agent) => void) => void
  addAgent: (agent: Agent) => void
}

const AGENT_COLORS = ['#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#06b6d4']

export const useAgentStore = create<AgentState>()((set) => ({
  agents: new Map(),
  setAgents: (agents) =>
    set(() => {
      const map = new Map<string, Agent>()
      agents.forEach((a, i) => {
        a.color = AGENT_COLORS[i % AGENT_COLORS.length]
        map.set(a.id, a)
      })
      return { agents: map }
    }),
  updateAgent: (id, updater) =>
    set((state) => {
      const agent = state.agents.get(id)
      if (agent) updater(agent)
      return state
    }),
  addAgent: (agent) =>
    set((state) => {
      const idx = state.agents.size
      agent.color = AGENT_COLORS[idx % AGENT_COLORS.length]
      state.agents.set(agent.id, agent)
      return state
    }),
}))
