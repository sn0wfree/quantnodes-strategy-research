import { CheckCircle2, Circle, Clock, X, ListChecks } from 'lucide-react'
import { useTodoStore, type TodoItem, type TodoStatus } from '../../stores/todo'

const STATUS_STYLE: Record<TodoStatus, { icon: typeof Circle; className: string }> = {
  pending: { icon: Circle, className: 'text-slate-500' },
  in_progress: { icon: Clock, className: 'text-amber-400 animate-pulse' },
  completed: { icon: CheckCircle2, className: 'text-emerald-400' },
}

const STATUS_LABEL: Record<TodoStatus, string> = {
  pending: '待办',
  in_progress: '进行中',
  completed: '完成',
}

function TodoRow({ todo }: { todo: TodoItem }) {
  const style = STATUS_STYLE[todo.status] ?? STATUS_STYLE.pending
  const Icon = style.icon
  return (
    <li className="flex items-start gap-2 rounded px-2 py-1.5 hover:bg-slate-800/50">
      <Icon className={`mt-0.5 h-3.5 w-3.5 flex-shrink-0 ${style.className}`} />
      <div className="min-w-0 flex-1">
        <p
          className={`text-xs leading-relaxed ${
            todo.status === 'completed'
              ? 'text-slate-500 line-through'
              : 'text-slate-200'
          }`}
        >
          {todo.content}
        </p>
        <span className="text-[9px] uppercase tracking-wide text-slate-600">
          {STATUS_LABEL[todo.status] ?? todo.status}
        </span>
      </div>
    </li>
  )
}

/**
 * Todo drawer (opencode-style) — slides over the right edge of the chat
 * column when the agent starts tracking a multi-step task via
 * `todo_write`. Auto-opens on the first `todo_updated` SSE event.
 */
export function TodoDrawer() {
  const todos = useTodoStore((s) => s.todos)
  const drawerOpen = useTodoStore((s) => s.drawerOpen)
  const setDrawerOpen = useTodoStore((s) => s.setDrawerOpen)

  if (!drawerOpen) return null

  const total = todos.length
  const done = todos.filter((t) => t.status === 'completed').length
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div
      className="absolute inset-y-0 right-0 z-30 flex w-80 max-w-[85%] flex-col border-l border-slate-800 bg-slate-900/95 shadow-2xl backdrop-blur"
      data-testid="todo-drawer"
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2.5">
        <ListChecks className="h-4 w-4 text-primary-400" />
        <span className="text-xs font-medium text-slate-200">任务清单</span>
        <span className="ml-auto text-[10px] text-slate-500">
          {done}/{total}
        </span>
        <button
          onClick={() => setDrawerOpen(false)}
          className="flex h-5 w-5 items-center justify-center rounded text-slate-500 hover:bg-slate-800 hover:text-slate-300"
          title="关闭任务抽屉"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Progress bar */}
      <div className="border-b border-slate-800/60 px-3 py-2">
        <div className="h-1 w-full overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-primary-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* List */}
      {total === 0 ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center">
          <p className="text-xs text-slate-500">暂无任务</p>
        </div>
      ) : (
        <ul className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {todos.map((t) => (
            <TodoRow key={t.id} todo={t} />
          ))}
        </ul>
      )}
    </div>
  )
}
