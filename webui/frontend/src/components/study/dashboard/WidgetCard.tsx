/**
 * WidgetCard — wraps a single dashboard widget in a styled container
 * with a title bar and content area.
 */
import { Suspense } from 'react'
import type { DashboardWidget, WidgetDef } from './types'

interface WidgetCardProps {
  widget: DashboardWidget
  def: WidgetDef
  studyId: string
  summary: Record<string, unknown>
  editMode?: boolean
  onRemove?: () => void
}

function WidgetFallback() {
  return (
    <div className="flex h-24 items-center justify-center text-xs text-slate-500">
      <div className="h-3 w-3 animate-spin rounded-full border border-slate-600 border-t-slate-400" />
    </div>
  )
}

export function WidgetCard({
  widget,
  def,
  studyId,
  summary,
  editMode = false,
  onRemove,
}: WidgetCardProps) {
  const Component = def.component

  return (
    <div
      className="group relative rounded-xl border border-slate-800 bg-slate-900/60 shadow-soft transition-colors hover:border-slate-700"
      style={{ gridColumn: `span ${widget.span}` }}
    >
      {/* Title bar */}
      <div className="flex items-center justify-between border-b border-slate-800/60 px-4 py-2">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <span>{def.icon}</span>
          <span>{def.label}</span>
        </div>
        {editMode && (
          <button
            onClick={onRemove}
            className="text-xs text-slate-500 hover:text-rose-400 transition-colors"
            title="隐藏此 widget"
          >
            ✕
          </button>
        )}
      </div>

      {/* Content */}
      <div className="p-4">
        <Suspense fallback={<WidgetFallback />}>
          <Component
            studyId={studyId}
            summary={summary as never}
            config={widget.config}
          />
        </Suspense>
      </div>
    </div>
  )
}
