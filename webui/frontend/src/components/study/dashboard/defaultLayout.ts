/**
 * Default dashboard layout — the initial widget arrangement
 * before the user customises it.
 */
import { CONFIG_VERSION, GRID_COLUMNS } from './types'
import type { DashboardConfig, DashboardWidget } from './types'
import { WIDGET_REGISTRY } from './registry'

let orderCounter = 0

function w(
  type: string,
  span?: number,
  enabled?: boolean,
): DashboardWidget {
  const def = WIDGET_REGISTRY[type]
  return {
    id: `${type}`,
    type,
    enabled: enabled ?? def?.defaultEnabled ?? true,
    span: span ?? def?.defaultSpan ?? GRID_COLUMNS,
    order: orderCounter++,
  }
}

export function getDefaultLayout(): DashboardConfig {
  orderCounter = 0
  return {
    version: CONFIG_VERSION,
    columns: GRID_COLUMNS,
    widgets: [
      w('live-activity'),        // 0  — span 12
      w('objective', 8),         // 1  — span 8
      w('dag-flow'),             // 2  — span 12
      w('metrics-compare', 6),   // 3  — span 6
      w('round-history', 6),     // 4  — span 6
      w('study-chat'),           // 5  — span 12
      w('scoreboard'),           // 6  — span 12 (disabled by default)
      w('budget'),               // 7  — span 12 (disabled by default)
    ],
  }
}

/** Widget types that are enabled in the default layout */
export const DEFAULT_ENABLED_TYPES = Object.values(getDefaultLayout().widgets)
  .filter(w => w.enabled)
  .map(w => w.type)
