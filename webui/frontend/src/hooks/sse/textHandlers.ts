import type { TextPart } from '../../stores/chat'
import type { SSEHandler, SSEContext } from './types'

function collapseLastThinking(messageId: string, updateMessage: SSEContext['updateMessage']) {
  updateMessage(messageId, (msg) => {
    const last = msg.parts[msg.parts.length - 1]
    if (last && last.type === 'thinking') last.collapsed = true
  })
}

/**
 * Text streaming events — opencode-style 3-step text protocol.
 *
 * `text.started` seeds a text part with a server-issued `text_id`,
 * `text_delta` appends to that exact part (hard-break if id missing),
 * `text.ended` overrides with the authoritative final text (last write
 * wins). `findLast-by-id` keeps text routing correct when text and
 * tool calls interleave across LLM iterations.
 */
export const textStarted: SSEHandler = (data, { updateMessage }) => {
  const textId = data.text_id as string | undefined
  const messageId = data.message_id as string | undefined
  if (!textId || !messageId) return
  updateMessage(messageId, (msg) => {
    const existing = msg.parts.find(
      (p) => p.type === 'text' && (p as TextPart).id === textId,
    )
    if (!existing) {
      msg.parts.push({ type: 'text', id: textId, text: '' })
    }
  })
}

export const textDelta: SSEHandler = (data, { updateMessage, appendStreamingText }) => {
  const text = (data.text || data.delta) as string
  const textId = data.text_id as string | undefined
  const messageId = data.message_id as string | undefined
  if (text && messageId) {
    if (!textId) {
      // Protocol error: drop the chunk. Protects against future
      // regressions where backend forgets to emit text.started first.
      console.warn('[useSSE] text_delta without text_id, dropping chunk')
    } else {
      updateMessage(messageId, (msg) => {
        for (let i = msg.parts.length - 1; i >= 0; i--) {
          const p = msg.parts[i]
          if (p && p.type === 'text' && (p as TextPart).id === textId) {
            ;(p as TextPart).text += text
            return
          }
        }
        // Orphan: text.started hasn't arrived yet (replay / late
        // join). Push a new part with this id to keep the chunk.
        msg.parts.push({ type: 'text', id: textId, text })
      })
    }
  }
  // Also update the global streaming text for the StreamingText component
  appendStreamingText(text || '')
}

export const textEnded: SSEHandler = (data, { updateMessage }) => {
  // Guard: some protocol variants emit text.ended as a pure
  // end-signal without the final text — never wipe the
  // accumulated streaming content with an empty string (B4).
  const textId = data.text_id as string | undefined
  const finalText = (data.text || '') as string
  const messageId = data.message_id as string | undefined
  if (!textId || !messageId) return
  updateMessage(messageId, (msg) => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i]
      if (p && p.type === 'text' && (p as TextPart).id === textId) {
        if (finalText) (p as TextPart).text = finalText
        return
      }
    }
  })
}

/**
 * Thinking events — append thinking tokens to the last thinking part.
 *
 * `thinking_start` pushes a fresh collapsed thinking part; subsequent
 * `thinking_delta` events append tokens; `thinking_done` / `thinking_end`
 * mark the part collapsed (UI auto-folds once the answer stream lands).
 */
export const thinkingStart: SSEHandler = (data, { updateMessage }) => {
  const messageId = data.message_id as string | undefined
  if (!messageId) return
  updateMessage(messageId, (msg) => {
    msg.parts.push({ type: 'thinking', text: '', collapsed: true } as any)
  })
}

export const thinkingDelta: SSEHandler = (data, { updateMessage }) => {
  const delta = data.delta as string
  const messageId = data.message_id as string | undefined
  if (!delta || !messageId) return
  updateMessage(messageId, (msg) => {
    const last = msg.parts[msg.parts.length - 1]
    if (last && last.type === 'thinking') last.text += delta
  })
}

export const thinkingDone: SSEHandler = (data, { updateMessage }) => {
  const messageId = data.message_id as string | undefined
  if (messageId) collapseLastThinking(messageId, updateMessage)
}

export const thinkingEnd: SSEHandler = (data, { updateMessage }) => {
  const messageId = data.message_id as string | undefined
  if (messageId) collapseLastThinking(messageId, updateMessage)
}