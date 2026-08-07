import { create } from 'zustand'

export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled'

export interface TodoItem {
  id: string
  content: string
  status: TodoStatus
}

interface TodoState {
  /** Full snapshot of the session's todo list (source: todo_updated SSE). */
  todos: TodoItem[]
  /** Whether the panel is expanded (showing the full list). When false, only
   * the 42px header with count + active todo preview is visible. */
  expanded: boolean
  /** Per-session "user has seen the panel for this session at least once"
   * marker. Stored in sessionStorage so the auto-expand only happens on the
   * FIRST todo_updated event of a session — after that the user's choice is
   * respected (mirrors opencode's collapsed-state persistence). */
  hasSeenFor: (sessionId: string) => boolean
  markSeenFor: (sessionId: string) => void
  /** Replace the whole list (server pushes full snapshots). When `expand` is
   * true and the panel is currently collapsed AND this is the first time
   * the user has seen todos for this session, auto-expand. */
  replaceTodos: (sessionId: string, todos: TodoItem[], opts?: { expand?: boolean }) => void
  setExpanded: (expanded: boolean) => void
  toggleExpanded: () => void
  clearTodos: () => void
}

const SEEN_KEY_PREFIX = 'strategy-research:todo-seen:'

function readSeen(sessionId: string): boolean {
  try {
    return sessionStorage.getItem(SEEN_KEY_PREFIX + sessionId) === '1'
  } catch {
    return false
  }
}

function writeSeen(sessionId: string): void {
  try {
    sessionStorage.setItem(SEEN_KEY_PREFIX + sessionId, '1')
  } catch {
    // sessionStorage unavailable — fall back to in-memory only
  }
}

export const useTodoStore = create<TodoState>()((set, get) => ({
  todos: [],
  expanded: false,
  hasSeenFor: (sessionId) => readSeen(sessionId),
  markSeenFor: (sessionId) => writeSeen(sessionId),
  replaceTodos: (sessionId, todos, opts) => {
    const s = get()
    const expandRequested = opts?.expand === true
    // Only auto-expand on the first real todo snapshot for a session —
    // an empty list (malformed payload filtered to nothing, or the agent
    // clearing the list) shouldn't pop the panel open.
    const shouldAutoExpand =
      expandRequested &&
      !s.expanded &&
      !readSeen(sessionId) &&
      todos.length > 0
    if (shouldAutoExpand) writeSeen(sessionId)
    set({
      todos,
      expanded: shouldAutoExpand ? true : s.expanded,
    })
  },
  setExpanded: (expanded) => set({ expanded }),
  toggleExpanded: () => set((s) => ({ expanded: !s.expanded })),
  clearTodos: () => set({ todos: [], expanded: false }),
}))