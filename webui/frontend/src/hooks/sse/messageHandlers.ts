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

// ── block-part handlers (file_edit / table / chart / image) ───────
//
// The backend AgentLoop does NOT currently emit these events at SSE
// time (see api/session/event_v2.py and projector.py:985-994 — the
// projector persists them as a defense-in-depth measure if/when
// emission lands). But the front-end SSE dispatcher registers the
// event names so any future emitter lands without code changes.
//
// When such an event arrives:
//   * we attach a stable part to the assistant message so the
//     matching <FileEditBlock> / <TableBlock> / <ChartBlock> /
//     <ImageBlock> renders inside AssistantMessage.PartRenderer
//   * dedup by part id (the event may include `id` or fall back to
//     `<type>_<seq>`)
//   * missing message_id is a no-op (the event has no UI home)

interface BlockPartEvent {
  message_id?: string
  id?: string
  // file_edit
  file_path?: string
  old_content?: string
  new_content?: string
  // table
  headers?: string[]
  rows?: string[][]
  caption?: string
  // chart
  chart_type?: 'bar' | 'line' | 'pie' | 'scatter'
  data?: unknown[]
  title?: string
  // image
  url?: string
  alt?: string
  // html
  content?: string
}

function attachBlockPart(
  data: BlockPartEvent,
  ctx: Parameters<SSEHandler>[1],
  typeLabel: string,
  build: (id: string) => unknown,
): void {
  const { message_id: mid, id: rawId } = data
  if (!mid) return
  const partId = rawId || `${typeLabel}_${Date.now()}`
  ctx.updateMessage(mid, (msg) => {
    if (msg.parts.some((p) => 'id' in p && p.id === partId)) return
    msg.parts.push(build(partId) as never)
  })
}

export const fileEdit: SSEHandler = (data, ctx) => {
  attachBlockPart(
    data,
    ctx,
    'file_edit',
    (id) => ({
      type: 'file_edit' as const,
      id,
      file_path: (data as BlockPartEvent).file_path ?? '',
      old_content: (data as BlockPartEvent).old_content ?? '',
      new_content: (data as BlockPartEvent).new_content ?? '',
    }),
  )
}

export const table: SSEHandler = (data, ctx) => {
  attachBlockPart(
    data,
    ctx,
    'table',
    (id) => ({
      type: 'table' as const,
      id,
      headers: (data as BlockPartEvent).headers ?? [],
      rows: (data as BlockPartEvent).rows ?? [],
      caption: (data as BlockPartEvent).caption,
    }),
  )
}

export const chart: SSEHandler = (data, ctx) => {
  attachBlockPart(
    data,
    ctx,
    'chart',
    (id) => ({
      type: 'chart' as const,
      id,
      chart_type: (data as BlockPartEvent).chart_type ?? 'bar',
      data: (data as BlockPartEvent).data ?? [],
      title: (data as BlockPartEvent).title,
    }),
  )
}

export const image: SSEHandler = (data, ctx) => {
  attachBlockPart(
    data,
    ctx,
    'image',
    (id) => ({
      type: 'image' as const,
      id,
      url: (data as BlockPartEvent).url ?? '',
      alt: (data as BlockPartEvent).alt,
    }),
  )
}

export const html: SSEHandler = (data, ctx) => {
  attachBlockPart(
    data,
    ctx,
    'html',
    (id) => ({
      type: 'html' as const,
      id,
      title: (data as BlockPartEvent).title,
      content: (data as BlockPartEvent).content ?? '',
    }),
  )
}

/**
 * goal_updated: backend emits a FULL snapshot after every goal
 * mutation (chat tools / /goal command / REST). Two consumers:
 * 1. metaHandlers.goalUpdated → right-panel GoalCard (setGoal)
 * 2. THIS handler → chat stream GoalMessage card (addMessage)
 *
 * The message id comes from the backend payload (message_id), which
 * is ALSO the id the projector persists — so live additions and DB
 * reloads share the same key (addMessage Map.set overwrites → no
 * duplicates on SSE replay).
 */
export const goalUpdatedMessage: SSEHandler = (data, ctx) => {
  const { addMessage, sessionId } = ctx
  const mid = data.message_id as string | undefined
  const goalId = data.goal_id as string | undefined
  if (!mid || !goalId || !sessionId) return

  const criteria = Array.isArray(data.criteria)
    ? (data.criteria as Array<{
        criterion_id: string
        text: string
        status: string
        evidence_count: number
      }>)
    : []

  addMessage({
    id: mid,
    session_id: sessionId,
    role: 'system',
    parts: [],
    created_at: Date.now() / 1000,
    message_type: 'goal',
    metadata: {
      goal_id: goalId,
      change_type: (data.change_type as string) || 'update',
      objective: (data.objective as string) || '',
      progress_percent: (data.progress_percent as number) ?? 0,
      goal_status: (data.goal_status as string) || 'active',
      criteria,
      evidence_count: (data.evidence_count as number) ?? 0,
      evidence_text: (data.evidence_text as string) || '',
      recap: (data.recap as string) || '',
    },
  })
}