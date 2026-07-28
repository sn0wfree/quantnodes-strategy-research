import { describe, it, expect } from 'vitest'
import { useAgentStore, type Agent } from '../stores/agents'

describe('useAgentStore', () => {
  it('adds an agent with auto-assigned color', () => {
    const agent: Agent = {
      id: 'a1',
      session_id: 's1',
      status: 'pending',
      name: 'Test Agent',
      created_at: 1,
      updated_at: 1,
      tool_calls_count: 0,
      compaction_count: 0,
      context_tokens: 0,
      context_tokens_limit: 8000,
      iterations_detail: [],
    }

    useAgentStore.getState().addAgent(agent)

    const stored = useAgentStore.getState().agents.get('a1')
    expect(stored).toBeDefined()
    expect(stored?.color).toBeDefined()
  })

  it('updates an agent', () => {
    const agent: Agent = {
      id: 'a2',
      session_id: 's1',
      status: 'running',
      name: 'Agent 2',
      created_at: 1,
      updated_at: 1,
      tool_calls_count: 0,
      compaction_count: 0,
      context_tokens: 0,
      context_tokens_limit: 8000,
      iterations_detail: [],
    }

    useAgentStore.getState().addAgent(agent)
    useAgentStore.getState().updateAgent('a2', (a) => {
      a.status = 'completed'
      a.tool_calls_count = 5
    })

    const stored = useAgentStore.getState().agents.get('a2')
    expect(stored?.status).toBe('completed')
    expect(stored?.tool_calls_count).toBe(5)
  })

  it('setAgents assigns colors in order', () => {
    const agents: Agent[] = [
      {
        id: 'b1', session_id: 's1', status: 'pending', name: 'B1',
        created_at: 1, updated_at: 1,
        tool_calls_count: 0, compaction_count: 0,
        context_tokens: 0, context_tokens_limit: 8000,
        iterations_detail: [],
      },
      {
        id: 'b2', session_id: 's1', status: 'pending', name: 'B2',
        created_at: 2, updated_at: 2,
        tool_calls_count: 0, compaction_count: 0,
        context_tokens: 0, context_tokens_limit: 8000,
        iterations_detail: [],
      },
    ]

    useAgentStore.getState().setAgents(agents)
    const state = useAgentStore.getState()
    expect(state.agents.get('b1')?.color).not.toBe(state.agents.get('b2')?.color)
  })
})