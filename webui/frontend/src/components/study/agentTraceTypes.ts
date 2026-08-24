/**
 * AgentTrace — structured data model for study chat views.
 *
 * Replaces raw history event replay with a clean, structured
 * representation of each agent's execution.
 */

export interface AgentTrace {
  agentId: string
  agentName: string
  icon: string
  color: string
  category: string
  status: 'completed' | 'max_iterations' | 'error'
  iterations: number
  maxIterations: number
  toolCalls: ToolCallInfo[]
  thinkingBlocks: ThinkingBlock[]
  finalOutputs: string[]
  errorOutput?: string
  elapsedSeconds?: number
  timestamp: number
}

export interface ToolCallInfo {
  id: string
  tool: string
  arguments: Record<string, unknown>
  result?: unknown
  status: 'ok' | 'error'
  iteration?: number
}

export interface ThinkingBlock {
  text: string
  iteration: number
  collapsed: boolean
}

/** View mode for study chat display. */
export type StudyChatViewMode = 'card' | 'compact' | 'timeline'
