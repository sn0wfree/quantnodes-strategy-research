import type { SSEHandler } from './types'

/**
 * session_total_tokens: backend authoritative figure for the current
 * attempt. Carries both cumulative spend (total_tokens) and the current
 * context-window occupancy (context_used = size of the prompt most
 * recently sent to the model). ContextUsageBar needs the BOUNDED
 * occupancy, not the cumulative spend — so we store context_used
 * (falling back to total_tokens defensively). Marks the session so
 * later llm_usage deltas are not applied on top (regression B2).
 */
export const sessionTotalTokens: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, markTotalTokensSeen } = ctx
  const { context_used, total_tokens } = data as {
    context_used?: number
    total_tokens?: number
  }
  if (!sessionId) return
  const used = typeof context_used === 'number' ? context_used : total_tokens
  if (typeof used !== 'number') return
  setTokensUsed(sessionId, used)
  markTotalTokensSeen(sessionId)
}

/**
 * llm_usage: per-call usage delta. The backend accumulates spend into
 * session_total_tokens and re-emits it for every LLM call, so adding
 * here would double-count (regression B2). The current context
 * occupancy is "the prompt size of the most recent call" — an overwrite,
 * not an accumulate. Only act as a fallback when the authoritative
 * session_total_tokens was never seen for this session.
 */
export const llmUsage: SSEHandler = (data, ctx) => {
  const { sessionId, setTokensUsed, state } = ctx
  if (!sessionId || state.hasSeenTotalTokens(sessionId)) return
  const d = data as {
    prompt_tokens?: number
    input_tokens?: number
    total_tokens?: number
  }
  const used =
    d.prompt_tokens ?? d.input_tokens ?? d.total_tokens
  if (typeof used === 'number' && used > 0) {
    setTokensUsed(sessionId, used)
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

/**
 * Defensive cleanup: clear `isStreaming` on every part of every
 * assistant message. The per-protocol handlers (text.ended /
 * thinking_done / tool_result) normally do this, but on disconnect,
 * cancel, or error the terminal event may never arrive — this keeps
 * the UI from showing stuck `running` spinners or perpetually
 * expanding thinking blocks.
 */
function clearAllStreamingParts(ctx: Parameters<SSEHandler>[1]): void {
  for (const msg of ctx.state.getMessages()) {
    if (msg.role !== 'assistant') continue
    for (const part of msg.parts) {
      if ((part as { isStreaming?: boolean }).isStreaming) {
        ;(part as { isStreaming?: boolean }).isStreaming = false
      }
    }
  }
}

/** agent_done: AgentLoop finished — clear streaming state. */
export const agentDone: SSEHandler = (_data, ctx) => {
  clearAllStreamingParts(ctx)
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
}

/** error: backend surfaced a fatal error — toast + clear streaming. */
export const errorEvent: SSEHandler = (data, ctx) => {
  const error = data.error as string
  if (error) ctx.addToast('error', error)
  clearAllStreamingParts(ctx)
  ctx.setStreamingMessage(null)
  ctx.setActiveAttempt(null)
}