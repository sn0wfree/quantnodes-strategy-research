/**
 * Study Dashboard Store — manages the configurable widget layout
 * for the study detail page.  Persisted to localStorage per study.
 */
import { create } from 'zustand'
import type { DashboardConfig, DashboardWidget } from '../components/study/dashboard/types'
import { CONFIG_VERSION, GRID_COLUMNS, STORAGE_KEY_PREFIX } from '../components/study/dashboard/types'
import { getDefaultLayout } from '../components/study/dashboard/defaultLayout'
import { WIDGET_REGISTRY } from '../components/study/dashboard/registry'

// ── localStorage helpers ─────────────────────────────────────────

function storageKey(studyId: string): string {
  return `${STORAGE_KEY_PREFIX}-${studyId}`
}

function saveConfig(studyId: string, config: DashboardConfig): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(storageKey(studyId), JSON.stringify(config))
  } catch {
    // localStorage full or blocked — silently ignore
  }
}

// ── Migration helpers ────────────────────────────────────────────

/** Create a default widget entry from its registry definition. */
function createWidgetEntry(type: string, order: number): DashboardWidget {
  const def = WIDGET_REGISTRY[type]
  return {
    id: type,
    type,
    enabled: def?.defaultEnabled ?? true,
    span: def?.defaultSpan ?? GRID_COLUMNS,
    order,
  }
}

/**
 * Migrate a saved config to the current schema.
 *
 * Behavior:
 * - Wrong version → return fresh default layout (old prefs discarded)
 * - Correct version but missing widget types → append them with
 *   registry defaults (respect user's existing order/spans/enabled flags)
 * - No changes needed → return the saved config unchanged
 */
function migrateConfig(saved: DashboardConfig): DashboardConfig {
  // Version mismatch → reset
  if (saved.version !== CONFIG_VERSION || !Array.isArray(saved.widgets)) {
    return getDefaultLayout()
  }

  // Find missing widget types
  const existing = new Set(saved.widgets.map((w) => w.type))
  const missingTypes = Object.keys(WIDGET_REGISTRY).filter((t) => !existing.has(t))
  if (missingTypes.length === 0) return saved

  // Re-normalize order to 0..N-1, preserving relative order
  const renumbered = saved.widgets.map((w, i) => ({ ...w, order: i }))
  const startOrder = renumbered.length
  const additions = missingTypes.map((t, i) => createWidgetEntry(t, startOrder + i))
  return { ...saved, widgets: [...renumbered, ...additions] }
}

/**
 * Read config from localStorage and apply migration.
 * Returns the (possibly migrated) config + a flag indicating whether
 * the config was modified.
 */
function loadConfig(studyId: string): { config: DashboardConfig; migrated: boolean } {
  if (typeof window === 'undefined') {
    return { config: getDefaultLayout(), migrated: false }
  }
  let saved: DashboardConfig | null = null
  try {
    const raw = localStorage.getItem(storageKey(studyId))
    if (raw) {
      const parsed = JSON.parse(raw) as DashboardConfig
      if (parsed.version === CONFIG_VERSION && Array.isArray(parsed.widgets)) {
        saved = parsed
      }
    }
  } catch {
    // ignore parse errors
  }
  if (!saved) {
    return { config: getDefaultLayout(), migrated: false }
  }
  const migrated = migrateConfig(saved)
  return { config: migrated, migrated: migrated !== saved }
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
    const { config, migrated } = loadConfig(studyId)
    if (migrated) {
      // Persist the migrated config so we only migrate once
      saveConfig(studyId, config)
    }
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
