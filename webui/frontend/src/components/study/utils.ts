/**
 * Shared date/time formatting utilities for the study components.
 */

/**
 * Format an ISO timestamp as ``YYYY-MM-DD HH:mm`` in the local timezone.
 *
 * Returns ``"—"`` when the input is missing or unparseable.
 */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return '—'
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

/**
 * Format an ISO timestamp as ``HH:mm:ss``. Useful for short in-day events
 * like round timelines.
 */
export function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return '—'
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  } catch {
    return '—'
  }
}

/**
 * Clamp a possibly-zero round number to at least 1. Used because
 * ``current_round ?? 1`` still returns 0 when the DB has a real 0
 * (``??`` only guards null/undefined, not falsy values).
 */
export function clampRound(round: number | null | undefined): number {
  return Math.max(1, round ?? 1)
}