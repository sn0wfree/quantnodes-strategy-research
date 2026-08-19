import type { StudySummaryResponse } from '../../api/client'

// ── Node status (shared across widgets) ──────────────────────────
export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

// ── Widget definition (registry entry) ───────────────────────────
export interface WidgetDef {
  /** Unique widget type identifier (e.g. "live-activity") */
  id: string
  /** Human-readable label */
  label: string
  /** Icon emoji or lucide name */
  icon: string
  /** Whether this widget is enabled by default */
  defaultEnabled: boolean
  /** Default width in grid units (1-12) */
  defaultSpan: number
  /** Minimum allowed width */
  minSpan?: number
  /** Maximum allowed width */
  maxSpan?: number
  /** The React component that renders this widget */
  component: React.FC<WidgetProps>
}

// ── Widget instance (user configuration) ─────────────────────────
export interface DashboardWidget {
  /** Unique instance ID (e.g. "live-activity" or "custom-1") */
  id: string
  /** Widget type key → registry lookup */
  type: string
  /** Whether this widget is visible */
  enabled: boolean
  /** Width in grid units (1-12) */
  span: number
  /** Sort order (lower = earlier) */
  order: number
  /** Widget-specific configuration */
  config?: Record<string, unknown>
}

// ── Dashboard layout configuration ───────────────────────────────
export interface DashboardConfig {
  /** Schema version for migration */
  version: number
  /** Number of grid columns (fixed: 12) */
  columns: number
  /** Widget instances */
  widgets: DashboardWidget[]
}

// ── Props passed to every widget component ───────────────────────
export interface WidgetProps {
  /** Study ID */
  studyId: string
  /** Full study summary from API */
  summary: StudySummaryResponse
  /** Widget-specific config (from DashboardWidget.config) */
  config?: Record<string, unknown>
}

// ── Live event (for event timeline widget) ───────────────────────
export interface LiveEvent {
  /** Event category */
  type: 'phase' | 'agent' | 'knowledge' | 'review' | 'retry' | 'evidence' | 'directive' | 'other'
  /** Human-readable message */
  message: string
  /** Timestamp (Date.now()) */
  timestamp: number
  /** Associated round number */
  round?: number
}

// ── Constants ────────────────────────────────────────────────────
export const GRID_COLUMNS = 12
export const STORAGE_KEY_PREFIX = 'sr-study-dashboard'
export const CONFIG_VERSION = 1
