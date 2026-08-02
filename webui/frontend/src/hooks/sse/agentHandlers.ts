import type { SSEHandler } from './types'

/**
 * Agent / DAG / progress events.
 *
 * TODO(architecture): agent_status / agent_loop / dag_update handlers
 * only UPDATE existing entries the store already has; nothing calls
 * addAgent / setDAG with real data (setDAG is only invoked with []),
 * so after a page reload the Agent list and DAG panels are empty
 * until a new run starts. Planned fix: backfill on session load from
 * the run's persisted state (see B13).
 */
export const agentStatus: SSEHandler = (data, { updateAgent }) => {
  const { agent_id, status, ...rest } = data as {
    agent_id: string
    status: string
    [key: string]: unknown
  }
  updateAgent(agent_id, (agent: any) => {
    agent.status = status as any
    Object.assign(agent, rest)
  })
}

export const agentLoop: SSEHandler = (data, { updateAgent }) => {
  const { agent_id, ...loopData } = data as {
    agent_id: string
    [key: string]: unknown
  }
  updateAgent(agent_id, (agent: any) => {
    Object.assign(agent, loopData)
  })
}

export const dagUpdate: SSEHandler = (data, { updateNodeStatus }) => {
  const { node_id, status } = data as { node_id: string; status: string }
  updateNodeStatus(node_id, status as any)
}

export const progress: SSEHandler = (data, ctx) => {
  const { progress } = data as { progress: number }
  ctx.setExecutionProgress(progress)
}