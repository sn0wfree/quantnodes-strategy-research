import type { TextPart, ToolCallPart } from '../../stores/chat'
import type { SSEHandler } from './types'

/**
 * Tool call lifecycle: `tool_call` seeds a pending tool_call part
 * (dedup by id), `tool_progress` attaches step list, `tool_result`
 * marks done/error with the JSON-serialized result.
 *
 * Args/result are always stored as JSON strings for consistency with
 * DB-loaded messages (which round-trip through JSON columns).
 */
export const toolCall: SSEHandler = (data, { updateMessage }) => {
  const { message_id: mid, id, name, arguments: rawArgs } = data as {
    message_id: string
    id: string
    name: string
    arguments: string | unknown
  }
  const args =
    typeof rawArgs === 'string' ? rawArgs : JSON.stringify(rawArgs ?? {})
  if (!mid) return
  updateMessage(mid, (msg) => {
    const existing = msg.parts.find(
      (p) => p.type === 'tool_call' && p.id === id,
    ) as ToolCallPart | undefined
    if (!existing) {
      msg.parts.push({
        type: 'tool_call',
        id,
        name,
        arguments: args,
        status: 'running',
        isStreaming: true,
      })
    } else {
      // Re-mark on replay (defensive).
      existing.isStreaming = true
    }
  })
}

export const toolResult: SSEHandler = (data, { updateMessage }) => {
  const { message_id: mid, id, result: rawResult, status } = data as {
    message_id: string
    id: string
    result: string | unknown
    status: string
  }
  if (!mid) return
  const result =
    typeof rawResult === 'string' ? rawResult : JSON.stringify(rawResult ?? {})
  updateMessage(mid, (msg) => {
    const tc = msg.parts.find((p) => p.type === 'tool_call' && p.id === id)
    if (tc && tc.type === 'tool_call') {
      tc.result = result
      tc.status = status as 'done' | 'error'
      tc.isStreaming = false
    }
  })
}

export const toolProgress: SSEHandler = (data, { updateMessage }) => {
  const { message_id: mid, id, steps } = data as {
    message_id: string
    id: string
    steps: string[]
  }
  if (!mid || !id || !steps) return
  updateMessage(mid, (msg) => {
    const tc = msg.parts.find((p) => p.type === 'tool_call' && p.id === id)
    if (tc && tc.type === 'tool_call') tc.progress = steps
  })
}

/**
 * assistant_message: backend finalizes an assistant turn.
 *
 * Two paths:
 * 1. Error message (message_type='error'): no streaming text — the
 *    message may not exist yet. Create it as an error bubble with
 *    friendly text + collapsible detail.
 * 2. Normal: find the LAST text part (by id) and replace if the new
 *    content is longer — preserves text_id-routing semantics and
 *    prevents max_iter from wiping accumulated text_delta content.
 */
export const assistantMessage: SSEHandler = (data, ctx) => {
  const { addMessage, updateMessage, sessionId, state } = ctx
  const messageId = data.message_id as string | undefined
  const content = data.content as string
  const messageType = data.message_type as string | undefined

  if (messageId && messageType === 'error' && content) {
    const meta = data.metadata as { details?: string } | undefined
    const details = meta?.details ?? ''
    const existing = state.getMessage(messageId)
    if (existing) {
      updateMessage(messageId, (msg) => {
        msg.message_type = 'error'
        msg.parts = [{ type: 'text', id: `err-${messageId}`, text: content }]
        if (!msg.metadata) msg.metadata = {}
        msg.metadata.status = 'error'
        msg.metadata.details = details
      })
    } else {
      addMessage({
        id: messageId,
        session_id: sessionId!,
        role: 'assistant',
        parts: [{ type: 'text', id: `err-${messageId}`, text: content }],
        created_at: Date.now() / 1000,
        message_type: 'error',
        metadata: { status: 'error', details },
      })
    }
    return
  }

  if (!content || !messageId) return
  updateMessage(messageId, (msg) => {
    // Find last text part with content (skip empty seeded ones)
    let lastTextIdx = -1
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'text') {
        lastTextIdx = i
        break
      }
    }
    if (lastTextIdx >= 0) {
      const p = msg.parts[lastTextIdx] as TextPart
      if (content.length > p.text.length || p.text === '') p.text = content
    }
  })
}