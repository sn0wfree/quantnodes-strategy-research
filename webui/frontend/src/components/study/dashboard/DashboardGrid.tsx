/**
 * DashboardGrid — renders the configurable widget layout.
 *
 * Reads from useStudyDashboardStore and renders enabled widgets
 * in a CSS Grid with 12 columns.
 */
import React from 'react'
import type { StudySummaryResponse } from '../../../api/client'
import { useStudyDashboardStore } from '../../../stores/studyDashboard'
import { WIDGET_REGISTRY } from './registry'
import { WidgetCard } from './WidgetCard'

interface DashboardGridProps {
  studyId: string
  summary: StudySummaryResponse
}

export function DashboardGrid({ studyId, summary }: DashboardGridProps) {
  const config = useStudyDashboardStore(s => s.config)
  const editMode = useStudyDashboardStore(s => s.editMode)
  const toggleWidget = useStudyDashboardStore(s => s.toggleWidget)

  if (!config) return null

  // Sort by order, filter enabled
  const visible = config.widgets
    .filter(w => w.enabled)
    .sort((a, b) => a.order - b.order)

  return (
    <div
      className="grid gap-4"
      style={{ gridTemplateColumns: `repeat(${config.columns}, minmax(0, 1fr))` }}
    >
      {visible.map(w => {
        const def = WIDGET_REGISTRY[w.type]
        if (!def) return null
        return (
          <WidgetCard
            key={w.type}
            widget={w}
            def={def}
            studyId={studyId}
            summary={summary}
            editMode={editMode}
            onRemove={() => toggleWidget(w.type)}
          />
        )
      })}

      {visible.length === 0 && (
        <div className="col-span-12 flex h-32 items-center justify-center text-sm text-slate-500">
          没有启用的 widget — 点击"编辑布局"添加
        </div>
      )}
    </div>
  )
}
