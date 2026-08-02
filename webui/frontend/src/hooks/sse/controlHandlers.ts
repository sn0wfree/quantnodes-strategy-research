import type { SSEHandler } from './types'

/**
 * session_total_tokens: backend authoritative cumulative for the
 * current attempt. Used by ContextUsageBar to show context window
 * usage. Marks the session so later llm_usage deltas are not added
 * on top (double counting — regression B2).
 */
export const sessionTotalTokens: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, markTotalTokensSeen } = ctx
  const { total_tokens } = data as { total_tokens: number }
  if (!sessionId || typeof total_tokens !== 'number') return
  setTokensUsed(sessionId, total_tokens)
  markTotalTokensSeen(sessionId)
}

/**
 * llm_usage: per-call usage delta. The backend accumulates these into
 * session_total_tokens and re-emits it for every LLM call, so adding
 * here would double-count (regression B2). Only fall back to deltas
 * when the authoritative cumulative event was never seen for this
 * session.
 */
export const llmUsage: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, state } = ctx
  if (!sessionId || state.hasSeenTotalTokens(sessionId)) return
  const d = data as {
    input_tokens?: number
    output_tokens?: number
    prompt_tokens?: number
    completion_tokens?: number
    total_tokens?: number
  }
  const inc =
    d.total_tokens ??
    (d.input_tokens ?? d.prompt_tokens ?? 0) +
      (d.output_tokens ?? d.completion_tokens ?? 0)
  if (inc > 0) {
    setTokensUsed(sessionId, state.getTokensUsed(sessionId) + inc)
  }
}

/**
 * compact: context compaction occurred. Increments the per-agent
 * compaction counter and surfaces a dismissible banner via
 * `setLastCompaction`.
 */
export const compact: SSEHandler = (data, ctx) => {
  const { updateAgent, setLastCompaction } = ctx
  const compactData = data as {
    agent_id?: string
    layer?: string
    iteration?: number
    summary?: string
  }
  if (compactData.agent_id) {
    updateAgent(compactData.agent_id, (agent: any) => {
      agent.compaction_count = (agent.compaction_count || 0) + 1
      agent.last_compaction = {
        layer: compactData.layer || 'unknown',
        timestamp: Date.now(),
      }
    })
  }
  setLastCompaction({
    layer: compactData.layer || 'unknown',
    timestamp: Date.now(),
  })
}

/** agent_done: AgentLoop finished — clear streaming state. */
export const agentDone: SSEHandler = (_data, ctx) => {
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
}

/** error: backend surfaced a fatal error — toast + clear streaming. */
export const errorEvent: SSEHandler = (data, ctx) => {
  const error = data.error as string
  if (error) ctx.addToast('error', error)
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
}