import type { Message } from '../stores/chat'

export interface EquityPoint {
  label: string
  value: number
}

export interface EquityCurve {
  title: string
  points: EquityPoint[]
  timestamp: number
}

/** Titles (case-insensitive) that mark a line chart as an equity/nav curve. */
const EQUITY_TITLE_PATTERNS: RegExp[] = [
  /净值/i,
  /nav/i,
  /equity/i,
  /收益曲线/i,
  /资产曲线/i,
  /曲线/i,
]

/**
 * Pick the latest equity/nav line chart from a session's messages and
 * decode it into ordered {label, value} points.
 *
 * A chart qualifies when it is a `line` chart whose title matches one of
 * the equity patterns. The x/y series keys are auto-detected from the
 * first data row (first numeric field → y, otherwise the first field).
 * Returns null when no qualifying chart is found.
 */
export function extractEquityCurve(messages: Message[]): EquityCurve | null {
  let best: { curve: EquityCurve } | null = null

  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'chart' || p.chart_type !== 'line') continue
      const title = (p.title ?? '').trim() || '净值曲线'
      const matches = EQUITY_TITLE_PATTERNS.some((re) => re.test(title))
      if (!matches) continue
      if (!Array.isArray(p.data) || p.data.length === 0) continue

      const points = decodePoints(p.data)
      if (points.length === 0) continue

      const curve = { title, points, timestamp: m.created_at }
      if (!best || curve.timestamp > best.curve.timestamp) {
        best = { curve }
      }
    }
  }

  return best ? best.curve : null
}

function decodePoints(data: unknown[]): EquityPoint[] {
  const first = data[0] as Record<string, unknown> | null
  if (!first || typeof first !== 'object') return []

  const keys = Object.keys(first)
  if (keys.length === 0) return []

  let yKey = keys.find((k) => typeof first[k] === 'number')
  let xKey = keys.find((k) => !!yKey && k !== yKey) ?? keys[0]
  if (!yKey) {
    // Fall back: any numeric key is y; x is the index.
    yKey = keys[keys.length - 1]
    xKey = keys[0]
  }
  // When only a single key exists it's both x and y — use the row index
  // as the x label instead of the value.
  const useIndexLabel = xKey === yKey

  const points: EquityPoint[] = []
  for (const row of data) {
    if (!row || typeof row !== 'object') continue
    const r = row as Record<string, unknown>
    const value = Number(r[yKey])
    if (!Number.isFinite(value)) continue
    const label = useIndexLabel ? String(points.length) : String(r[xKey])
    points.push({ label, value })
  }
  return points
}