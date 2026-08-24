/**
 * buildAgentTraces — convert API agent_outputs history into AgentTrace[].
 *
 * Filters internal events, strips <think> tags, merges tool_call+tool_result,
 * and extracts structured data for the three view modes.
 */

import { getAgentStyle } from './agentStyles'
import type { AgentTrace, ToolCallInfo, ThinkingBlock } from './agentTraceTypes'

// ── Internal event types (not rendered) ───────────────────────

const SKIP_EVENTS = new Set([
  'loop_start',
  'iter_start',
  'iter_end',
  'llm_request',
  'llm_response',
  'loop_end',
  'loop_final',
])

// ── `<think>` tag extraction ─────────────────────────────────

interface ParsedText {
  cleanText: string
  thinkingBlocks: ThinkingBlock[]
}

function extractThinkingTags(raw: string, iteration: number): ParsedText {
  const thinkingBlocks: ThinkingBlock[] = []
  let cleanText = ''
  let i = 0

  while (i < raw.length) {
    const thinkStart = raw.indexOf('<think>', i)
    if (thinkStart === -1) {
      cleanText += raw.slice(i)
      break
    }

    // Text before <think>
    cleanText += raw.slice(i, thinkStart)

    const thinkEnd = raw.indexOf('</think>', thinkStart + 7)
    if (thinkEnd === -1) {
      // Unclosed <think>
      thinkingBlocks.push({
        text: raw.slice(thinkStart + 7),
        iteration,
        collapsed: false,
      })
      break
    }

    thinkingBlocks.push({
      text: raw.slice(thinkStart + 7, thinkEnd),
      iteration,
      collapsed: true,
    })
    i = thinkEnd + 8
  }

  return { cleanText: cleanText.trim(), thinkingBlocks }
}

// ── Main builder ──────────────────────────────────────────────

export function buildAgentTraces(
  outputs: Record<string, unknown>,
  _studyId: string,
  _round: number,
): AgentTrace[] {
  if (!outputs) return []

  return Object.entries(outputs).map(([agentId, raw]) => {
    const out = raw as Record<string, unknown>
    const style = getAgentStyle(agentId)
    const history = (out.history as Array<{ type: string; data: Record<string, any>; ts: number }>) || []

    // ── State accumulators ──────────────────────────────
    let currentIteration = 0
    let maxIterations = 0
    let elapsedSeconds = 0
    let status: AgentTrace['status'] = 'completed'
    let currentText = ''
    const finalOutputs: string[] = []
    const allThinkingBlocks: ThinkingBlock[] = []
    const toolCalls: ToolCallInfo[] = []
    const toolCallMap = new Map<string, number>() // id → index in toolCalls

    // Track which text.ended we've already used (by text_id)
    const processedTextIds = new Set<string>()

    for (const evt of history) {
      const evtType = evt.type as string
      const d = evt.data || {}

      // Skip internal events
      if (SKIP_EVENTS.has(evtType)) {
        // Extract metadata from loop_final
        if (evtType === 'loop_final') {
          if (d.iterations) currentIteration = d.iterations
          if (d.elapsed_s) elapsedSeconds = Math.round(d.elapsed_s)
        }
        // Extract max_iterations from loop_start
        if (evtType === 'loop_start') {
          if (d.max_iterations) maxIterations = d.max_iterations
        }
        // Track iteration count from iter_start
        if (evtType === 'iter_start') {
          if (d.iteration > currentIteration) currentIteration = d.iteration
          if (d.max_iterations > maxIterations) maxIterations = d.max_iterations
        }
        continue
      }

      // ── Thinking events ───────────────────────────────
      if (evtType === 'thinking_start' || evtType === 'thinking_done') {
        // Handled by text_delta with <think> tags
        continue
      }

      if (evtType === 'thinking_end') {
        continue
      }

      // ── Text events ───────────────────────────────────
      if (evtType === 'text.started') {
        currentText = ''
        continue
      }

      if (evtType === 'text_delta') {
        const delta = d.text || d.delta || ''
        if (!delta) continue
        currentText += delta
        continue
      }

      if (evtType === 'text.ended') {
        const textId = d.text_id
        const fullText = d.text || currentText || ''
        if (!fullText) continue

        // Deduplicate: skip if we already processed this text_id
        if (textId && processedTextIds.has(textId)) continue
        if (textId) processedTextIds.add(textId)

        // Extract <think> tags
        const parsed = extractThinkingTags(fullText, currentIteration)
        allThinkingBlocks.push(...parsed.thinkingBlocks)

        if (parsed.cleanText) {
          finalOutputs.push(parsed.cleanText)
        }

        // Reset current text
        currentText = ''
        continue
      }

      // ── Tool call events ──────────────────────────────
      if (evtType === 'tool_call') {
        const tcId = d.id || `tc:${agentId}:${evt.ts}`
        const args = d.arguments || d.args || {}
        const parsedArgs = typeof args === 'string' ? tryParseJSON(args) : args
        const idx = toolCalls.length
        toolCalls.push({
          id: tcId,
          tool: d.tool || d.name || 'unknown',
          arguments: parsedArgs,
          status: 'ok', // Will be updated by tool_result
          iteration: currentIteration,
        })
        toolCallMap.set(tcId, idx)
        continue
      }

      if (evtType === 'tool_result') {
        const tcId = d.id || d.tool_call_id
        const idx = tcId ? toolCallMap.get(tcId) : undefined
        if (idx !== undefined && toolCalls[idx]) {
          toolCalls[idx].status = d.status === 'error' || d.ok === false ? 'error' : 'ok'
          toolCalls[idx].result = d.result || d.output || ''
        }
        continue
      }

      // ── Assistant message (max_iterations fallback) ───
      if (evtType === 'assistant_message') {
        const content = d.content || d.text || ''
        if (content && typeof content === 'string') {
          // This is typically the "Reached max_iterations=X" message
          // Only use as errorOutput if no finalOutputs were collected
          if (finalOutputs.length === 0) {
            finalOutputs.push(content)
          }
        }
        continue
      }
    }

    // ── Determine status ─────────────────────────────────
    const errorOutput = out.output as string | undefined
    if (errorOutput && errorOutput.includes('max_iterations')) {
      status = 'max_iterations'
    } else if (out.error) {
      status = 'error'
    }

    // If we have no finalOutputs but have an errorOutput, use it
    if (finalOutputs.length === 0 && errorOutput) {
      finalOutputs.push(errorOutput)
    }

    return {
      agentId,
      agentName: style.name,
      icon: style.icon,
      color: style.color,
      category: style.category,
      status,
      iterations: currentIteration || maxIterations,
      maxIterations,
      toolCalls,
      thinkingBlocks: allThinkingBlocks,
      finalOutputs,
      errorOutput,
      elapsedSeconds,
      timestamp: Date.now() / 1000,
    }
  })
}

// ── Build compact messages from traces ────────────────────────

import type { Message } from '../../stores/chat'

export function buildCompactMessages(
  traces: AgentTrace[],
  studyId: string,
  round: number,
): Message[] {
  return traces.map((trace) => {
    const parts: Message['parts'] = []

    // Summary line
    const statusEmoji = trace.status === 'completed' ? '✅'
      : trace.status === 'max_iterations' ? '⏱'
      : '❌'
    const summary = `${trace.icon} ${trace.agentName} · ${statusEmoji} ${trace.status === 'max_iterations' ? '超时' : trace.status === 'error' ? '错误' : '完成'} · ${trace.iterations}/${trace.maxIterations} 迭代 · ${trace.toolCalls.length} 工具`

    parts.push({
      type: 'text',
      id: `compact:summary:${studyId}:r${round}:${trace.agentId}`,
      text: summary,
    })

    // Final output
    if (trace.finalOutputs.length > 0) {
      const lastOutput = trace.finalOutputs[trace.finalOutputs.length - 1]
      parts.push({
        type: 'text',
        id: `compact:output:${studyId}:r${round}:${trace.agentId}`,
        text: lastOutput,
      })
    }

    // Error output
    if (trace.errorOutput && trace.status !== 'completed') {
      parts.push({
        type: 'text',
        id: `compact:error:${studyId}:r${round}:${trace.agentId}`,
        text: `> ${trace.errorOutput}`,
      })
    }

    // Thinking (collapsed)
    if (trace.thinkingBlocks.length > 0) {
      const allThinking = trace.thinkingBlocks.map(t => t.text).join('\n\n')
      parts.push({
        type: 'thinking',
        id: `compact:think:${studyId}:r${round}:${trace.agentId}`,
        text: allThinking,
        collapsed: true,
      } as any)
    }

    return {
      id: `compact:${studyId}:r${round}:${trace.agentId}`,
      session_id: `study:${studyId}:stream`,
      role: 'assistant' as const,
      agent_id: trace.agentId,
      parts,
      created_at: trace.timestamp,
      metadata: {
        model: trace.agentId,
        kind: 'agent' as const,
        round,
      },
    }
  })
}

// ── Helpers ───────────────────────────────────────────────────

function tryParseJSON(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s)
  } catch {
    return { raw: s }
  }
}
