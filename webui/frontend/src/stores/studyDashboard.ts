/**
 * Study Dashboard Store — manages the configurable widget layout
 * for the study detail page.  Persisted to localStorage per study.
 */
import { create } from 'zustand'
import type { DashboardConfig } from '../components/study/dashboard/types'
import { CONFIG_VERSION, GRID_COLUMNS, STORAGE_KEY_PREFIX } from '../components/study/dashboard/types'
import { getDefaultLayout } from '../components/study/dashboard/defaultLayout'

// ── localStorage helpers ─────────────────────────────────────────

function storageKey(studyId: string): string {
  return `${STORAGE_KEY_PREFIX}-${studyId}`
}

function loadConfig(studyId: string): DashboardConfig {
  if (typeof window === 'undefined') return getDefaultLayout()
  try {
    const raw = localStorage.getItem(storageKey(studyId))
    if (!raw) return getDefaultLayout()
    const parsed = JSON.parse(raw) as DashboardConfig
    // Basic validation
    if (parsed.version !== CONFIG_VERSION || !Array.isArray(parsed.widgets)) {
      return getDefaultLayout()
    }
    return parsed
  } catch {
    return getDefaultLayout()
  }
}

function saveConfig(studyId: string, config: DashboardConfig): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(storageKey(studyId), JSON.stringify(config))
  } catch {
    // localStorage full or blocked — silently ignore
  }
}

// ── Store ────────────────────────────────────────────────────────

interface DashboardState {
  /** Current dashboard config (null = not loaded yet) */
  config: DashboardConfig | null
  /** Whether the edit mode is active */
  editMode: boolean
  /** The study ID this config belongs to (set on load) */
  studyId: string | null

  /** Load config for a study (from localStorage or default) */
  load: (studyId: string) => void
  /** Toggle a widget on/off */
  toggleWidget: (type: string) => void
  /** Move a widget to a new position (drag reorder) */
  moveWidget: (fromIndex: number, toIndex: number) => void
  /** Change a widget's span (width) */
  setWidgetSpan: (type: string, span: number) => void
  /** Toggle edit mode */
  setEditMode: (on: boolean) => void
  /** Reset to default layout */
  resetLayout: () => void
  /** Clear config for a study */
  clear: () => void
}

export const useStudyDashboardStore = create<DashboardState>((set, get) => ({
  config: null,
  editMode: false,
  studyId: null,

  load: (studyId: string) => {
    const config = loadConfig(studyId)
    set({ config, studyId })
  },

  toggleWidget: (type: string) => {
    const { config, studyId } = get()
    if (!config || !studyId) return

    const widgets = config.widgets.map(w =>
      w.type === type ? { ...w, enabled: !w.enabled } : w,
    )
    const next = { ...config, widgets }
    saveConfig(studyId, next)
    set({ config: next })
  },

  moveWidget: (fromIndex: number, toIndex: number) => {
    const { config, studyId } = get()
    if (!config || !studyId) return

    const widgets = [...config.widgets]
    const [moved] = widgets.splice(fromIndex, 1)
    widgets.splice(toIndex, 0, moved)
    // Re-assign order
    const reordered = widgets.map((w, i) => ({ ...w, order: i }))
    const next = { ...config, widgets: reordered }
    saveConfig(studyId, next)
    set({ config: next })
  },

  setWidgetSpan: (type: string, span: number) => {
    const { config, studyId } = get()
    if (!config || !studyId) return

    const clamped = Math.max(1, Math.min(GRID_COLUMNS, span))
    const widgets = config.widgets.map(w =>
      w.type === type ? { ...w, span: clamped } : w,
    )
    const next = { ...config, widgets }
    saveConfig(studyId, next)
    set({ config: next })
  },

  setEditMode: (on: boolean) => {
    set({ editMode: on })
  },

  resetLayout: () => {
    const { studyId } = get()
    if (!studyId) return
    const next = getDefaultLayout()
    saveConfig(studyId, next)
    set({ config: next })
  },

  clear: () => {
    set({ config: null, studyId: null, editMode: false })
  },
}))
