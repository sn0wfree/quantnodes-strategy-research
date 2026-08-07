import { create } from 'zustand'

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  id: string
  content: string
  status: TodoStatus
}

interface TodoState {
  /** Full snapshot of the session's todo list (source: todo_updated SSE). */
  todos: TodoItem[]
  /** Whether the todo drawer is visible. Auto-opens on first event. */
  drawerOpen: boolean
  /** Replace the whole list (server pushes full snapshots). */
  replaceTodos: (todos: TodoItem[], opts?: { open?: boolean }) => void
  setDrawerOpen: (open: boolean) => void
  clearTodos: () => void
}

export const useTodoStore = create<TodoState>()((set) => ({
  todos: [],
  drawerOpen: false,
  replaceTodos: (todos, opts) =>
    set((s) => ({
      todos,
      drawerOpen: opts?.open ?? s.drawerOpen,
    })),
  setDrawerOpen: (open) => set({ drawerOpen: open }),
  clearTodos: () => set({ todos: [], drawerOpen: false }),
}))
