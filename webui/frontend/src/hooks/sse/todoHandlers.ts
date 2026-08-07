import type { SSEHandler } from './types'
import { useTodoStore, type TodoItem } from '../../stores/todo'

/**
 * Todo tracking SSE handler (opencode-style).
 *
 * Backend contract: the `todo_write` tool pushes a full snapshot on
 * every change via the `todo_updated` event:
 *
 *     data: { todos: [{ id, content, status }] }
 *
 * The frontend replaces the whole list and, on the FIRST event for a
 * session, auto-expands the panel so the user can follow long-horizon
 * task progress. Subsequent updates respect the user's collapsed /
 * expanded choice (mirrors opencode's per-session persistence).
 *
 * When every todo reaches a terminal state (completed / cancelled),
 * the panel collapses and clears itself after a short delay, matching
 * opencode's `closeMs=400` close animation.
 */

const AUTO_CLOSE_DELAY_MS = 400
let closeTimer: ReturnType<typeof setTimeout> | null = null

function scheduleAutoClose() {
  if (closeTimer) clearTimeout(closeTimer)
  closeTimer = setTimeout(() => {
    closeTimer = null
    useTodoStore.getState().clearTodos()
  }, AUTO_CLOSE_DELAY_MS)
}

function cancelAutoClose() {
  if (closeTimer) {
    clearTimeout(closeTimer)
    closeTimer = null
  }
}

export const todoUpdated: SSEHandler = (data, ctx) => {
  const { todos } = data as { todos?: TodoItem[] }
  if (!Array.isArray(todos)) return
  const sessionId = ctx?.sessionId ?? 'default'
  const normalized = todos.filter(
    (t) => t && typeof t.id === 'string' && typeof t.content === 'string'
  )

  const allDone =
    normalized.length > 0 &&
    normalized.every((t) => t.status === 'completed' || t.status === 'cancelled')

  if (allDone) {
    // Keep the snapshot visible briefly, then collapse + clear
    useTodoStore.getState().replaceTodos(sessionId, normalized, { expand: true })
    scheduleAutoClose()
    return
  }

  cancelAutoClose()
  useTodoStore.getState().replaceTodos(sessionId, normalized, { expand: true })
}