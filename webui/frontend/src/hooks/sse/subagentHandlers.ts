import type { SSEHandler } from './types'
import type { AgentPart, ToolCallPart } from '../../stores/chat'

/**
 * Subagent SSE event handlers.
 *
 * These handle the lifecycle of subagent execution within a chat session:
 * - subagent_started: A new subagent begins execution (creates AgentPart)
 * - subagent_tool_call: Subagent invokes a tool
 * - subagent_tool_result: Tool call completes
 * - subagent_text_delta: Streaming text from the subagent
 * - subagent_completed: Subagent finished successfully
 * - subagent_failed: Subagent encountered an error
 *
 * The backend is expected to emit these events via EventStore.emit()
 * when a subagent is spawned from the chat AgentLoop.
 */

export const subagentStarted: SSEHandler = (data, ctx) => {
  const { agent_id, name, message_id } = data as {
    agent_id: string
    name: string
    message_id: string
  }

  const agentPart: AgentPart = {
    type: 'agent',
    id: `agent-${agent_id}`,
    agentId: agent_id,
    name: name || agent_id,
    status: 'running',
    toolCalls: [],
    streamingText: '',
    startedAt: Date.now() / 1000,
    isStreaming: true,
  }

  ctx.updateMessage(message_id, (msg) => {
    msg.parts.push(agentPart)
  })
}

export const subagentToolCall: SSEHandler = (data, ctx) => {
  const { agent_id, tool_call_id, name, arguments: args } = data as {
    agent_id: string
    tool_call_id: string
    name: string
    arguments: string | unknown
  }

  const tc: ToolCallPart = {
    type: 'tool_call',
    id: tool_call_id,
    name,
    arguments: args,
    status: 'running',
    isStreaming: true,
  }

  // Find the message containing this agent's part
  for (const msg of ctx.state.getMessages()) {
    const agentPart = msg.parts.find(
      (p) => p.type === 'agent' && p.agentId === agent_id,
    ) as AgentPart | undefined
    if (agentPart) {
      ctx.updateMessage(msg.id, (m) => {
        const ap = m.parts.find(
          (p) => p.type === 'agent' && (p as AgentPart).agentId === agent_id,
        ) as AgentPart | undefined
        if (ap) ap.toolCalls.push(tc)
      })
      break
    }
  }
}

export const subagentToolResult: SSEHandler = (data, ctx) => {
  const { agent_id, tool_call_id, result, status } = data as {
    agent_id: string
    tool_call_id: string
    result: string | unknown
    status: 'done' | 'error'
  }

  for (const msg of ctx.state.getMessages()) {
    const agentPart = msg.parts.find(
      (p) => p.type === 'agent' && p.agentId === agent_id,
    ) as AgentPart | undefined
    if (agentPart) {
      ctx.updateMessage(msg.id, (m) => {
        const ap = m.parts.find(
          (p) => p.type === 'agent' && (p as AgentPart).agentId === agent_id,
        ) as AgentPart | undefined
        if (ap) {
          const tc = ap.toolCalls.find((t) => t.id === tool_call_id)
          if (tc) {
            tc.result = result
            tc.status = status
            tc.isStreaming = false
          }
        }
      })
      break
    }
  }
}

export const subagentTextDelta: SSEHandler = (data, ctx) => {
  const { agent_id, delta } = data as {
    agent_id: string
    delta: string
  }

  for (const msg of ctx.state.getMessages()) {
    const agentPart = msg.parts.find(
      (p) => p.type === 'agent' && p.agentId === agent_id,
    ) as AgentPart | undefined
    if (agentPart) {
      ctx.updateMessage(msg.id, (m) => {
        const ap = m.parts.find(
          (p) => p.type === 'agent' && (p as AgentPart).agentId === agent_id,
        ) as AgentPart | undefined
        if (ap) ap.streamingText += delta
      })
      break
    }
  }
}

export const subagentCompleted: SSEHandler = (data, ctx) => {
  const { agent_id, tokens_used } = data as {
    agent_id: string
    tokens_used?: number
  }

  for (const msg of ctx.state.getMessages()) {
    const agentPart = msg.parts.find(
      (p) => p.type === 'agent' && p.agentId === agent_id,
    ) as AgentPart | undefined
    if (agentPart) {
      ctx.updateMessage(msg.id, (m) => {
        const ap = m.parts.find(
          (p) => p.type === 'agent' && (p as AgentPart).agentId === agent_id,
        ) as AgentPart | undefined
        if (ap) {
          ap.status = 'completed'
          ap.finishedAt = Date.now() / 1000
          ap.isStreaming = false
          if (tokens_used !== undefined) ap.tokensUsed = tokens_used
        }
      })
      break
    }
  }
}

export const subagentFailed: SSEHandler = (data, ctx) => {
  const { agent_id, error } = data as {
    agent_id: string
    error: string
  }

  for (const msg of ctx.state.getMessages()) {
    const agentPart = msg.parts.find(
      (p) => p.type === 'agent' && p.agentId === agent_id,
    ) as AgentPart | undefined
    if (agentPart) {
      ctx.updateMessage(msg.id, (m) => {
        const ap = m.parts.find(
          (p) => p.type === 'agent' && (p as AgentPart).agentId === agent_id,
        ) as AgentPart | undefined
        if (ap) {
          ap.status = 'failed'
          ap.finishedAt = Date.now() / 1000
          ap.isStreaming = false
          ap.error = error
        }
      })
      break
    }
  }
}
