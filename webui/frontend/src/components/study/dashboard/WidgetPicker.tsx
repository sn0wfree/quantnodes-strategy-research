/**
 * WidgetPicker — slide-out panel for configuring the dashboard layout.
 *
 * Shows all available widgets with toggle switches, drag handles,
 * and span sliders.  Only visible when editMode is true.
 */
import React, { useCallback, useRef } from 'react'
import { useStudyDashboardStore } from '../../../stores/studyDashboard'
import { WIDGET_REGISTRY, WIDGET_ORDER } from './registry'
import { GRID_COLUMNS } from './types'

export function WidgetPicker() {
  const config = useStudyDashboardStore(s => s.config)
  const editMode = useStudyDashboardStore(s => s.editMode)
  const toggleWidget = useStudyDashboardStore(s => s.toggleWidget)
  const moveWidget = useStudyDashboardStore(s => s.moveWidget)
  const setWidgetSpan = useStudyDashboardStore(s => s.setWidgetSpan)
  const resetLayout = useStudyDashboardStore(s => s.resetLayout)
  const setEditMode = useStudyDashboardStore(s => s.setEditMode)

  const dragIndex = useRef<number | null>(null)

  const handleDragStart = useCallback((index: number) => {
    dragIndex.current = index
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent, index: number) => {
    e.preventDefault()
    if (dragIndex.current !== null && dragIndex.current !== index) {
      moveWidget(dragIndex.current, index)
      dragIndex.current = index
    }
  }, [moveWidget])

  const handleDragEnd = useCallback(() => {
    dragIndex.current = null
  }, [])

  if (!editMode || !config) return null

  // Build ordered list from config
  const ordered = [...config.widgets].sort((a, b) => a.order - b.order)

  return (
    <div className="w-72 flex-shrink-0 border-r border-slate-800 bg-slate-900/80 p-4 overflow-y-auto">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">编辑布局</h3>
        <button
          onClick={() => setEditMode(false)}
          className="text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          完成
        </button>
      </div>

      {/* Widget list */}
      <div className="space-y-2">
        {ordered.map((w, index) => {
          const def = WIDGET_REGISTRY[w.type]
          if (!def) return null
          return (
            <div
              key={w.type}
              draggable
              onDragStart={() => handleDragStart(index)}
              onDragOver={(e) => handleDragOver(e, index)}
              onDragEnd={handleDragEnd}
              className={`rounded-lg border p-3 transition-colors cursor-grab active:cursor-grabbing ${
                w.enabled
                  ? 'border-slate-700 bg-slate-800/60'
                  : 'border-slate-800 bg-slate-900/40 opacity-50'
              }`}
            >
              {/* Row 1: Toggle + Label + Drag handle */}
              <div className="flex items-center gap-2">
                {/* Drag handle */}
                <span className="text-slate-600 cursor-grab">⠿</span>

                {/* Toggle */}
                <button
                  onClick={() => toggleWidget(w.type)}
                  className={`relative h-5 w-9 flex-shrink-0 rounded-full transition-colors ${
                    w.enabled ? 'bg-emerald-600' : 'bg-slate-700'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                      w.enabled ? 'translate-x-4' : 'translate-x-0.5'
                    }`}
                  />
                </button>

                {/* Icon + Label */}
                <span className="text-sm">{def.icon}</span>
                <span className="text-sm text-slate-300">{def.label}</span>
              </div>

              {/* Row 2: Span slider (only when enabled) */}
              {w.enabled && (
                <div className="mt-2 flex items-center gap-2 pl-7">
                  <span className="text-xs text-slate-500">宽度</span>
                  <input
                    type="range"
                    min={def.minSpan ?? 1}
                    max={def.maxSpan ?? GRID_COLUMNS}
                    value={w.span}
                    onChange={(e) => setWidgetSpan(w.type, Number(e.target.value))}
                    className="h-1 flex-1 cursor-pointer accent-emerald-500"
                  />
                  <span className="w-6 text-center text-xs text-slate-400">
                    {w.span}
                  </span>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Footer */}
      <div className="mt-6 space-y-2">
        <button
          onClick={resetLayout}
          className="w-full rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-2 text-xs text-slate-400 transition-colors hover:border-slate-600 hover:text-slate-200"
        >
          恢复默认布局
        </button>
      </div>
    </div>
  )
}
