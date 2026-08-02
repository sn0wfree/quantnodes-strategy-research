/**
 * Shared status → label / badge-class mapping.
 *
 * The 中文 labels and pill colors for agent / DAG / goal / tool status
 * were previously duplicated in 5 components (DAGNode, DAGNodeDetail,
 * AgentItem, GoalTab, ToolCallBlock). This is the single source of
 * truth; components that need icons keep their own icon mapping.
 */

export const STATUS_LABELS: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
  aborted: '已中止',
  done: '已完成',
  error: '失败',
  active: '进行中',
  success: '成功',
}

/** Human-readable label for a status key (falls back to the raw key). */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

const BADGE_CLASSES: Record<string, string> = {
  pending: 'bg-slate-500/20 text-slate-400',
  running: 'bg-blue-500/20 text-blue-400',
  completed: 'bg-emerald-500/20 text-emerald-400',
  done: 'bg-emerald-500/20 text-emerald-400',
  success: 'bg-emerald-500/20 text-emerald-400',
  failed: 'bg-red-500/20 text-red-400',
  error: 'bg-red-500/20 text-red-400',
  skipped: 'bg-slate-600/20 text-slate-500',
  aborted: 'bg-orange-500/20 text-orange-400',
  active: 'bg-emerald-900/50 text-emerald-300',
}

/** Tailwind pill classes for a status key. */
export function statusBadgeClass(status: string): string {
  return BADGE_CLASSES[status] ?? 'bg-slate-500/20 text-slate-400'
}
