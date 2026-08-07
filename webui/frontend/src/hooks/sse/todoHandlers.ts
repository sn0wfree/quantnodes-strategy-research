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
 * The frontend replaces the whole list and auto-opens the drawer on
 * the first event so the user can follow long-horizon task progress.
 */

export const todoUpdated: SSEHandler = (data) => {
  const { todos } = data as { todos?: TodoItem[] }
  if (!Array.isArray(todos)) return
  const normalized = todos.filter(
    (t) => t && typeof t.id === 'string' && typeof t.content === 'string'
  )
  useTodoStore.getState().replaceTodos(normalized, { open: true })
}
