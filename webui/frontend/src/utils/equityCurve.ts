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

/**
 * Metrics extracted from a ``run_backtest`` tool_call result. Used
 * by EquityCurveCard's metrics-only fallback when no chart parts
 * are available (the backend AgentLoop does not currently emit
 * `chart` SSE events, so the curve is always empty in practice).
 */
export interface BacktestMetrics {
  total_return?: number
  sharpe?: number
  max_drawdown?: number
  annual_return?: number
  win_rate?: number
  /** Which run produced these metrics, for the card subtitle. */
  run?: string
  strategy?: string
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

/**
 * Pick the latest ``run_backtest`` tool_call result and decode its
 * metrics. Used as the metrics-only fallback in EquityCurveCard when
 * no chart parts are available (the AgentLoop does not currently emit
 * ``chart`` SSE events; see projector.py:985-994).
 *
 * The tool result payload is the JSON string returned by
 * ``builtin_tools.RunBacktestTool.execute``:
 *     { run, strategy, metrics, status }
 * where ``metrics`` is a flat dict (total_return, sharpe,
 * max_drawdown, ...). The parser is defensive — any failure yields
 * null, not a crash.
 */
export function extractLatestBacktestMetrics(
  messages: Message[],
): BacktestMetrics | null {
  let best: { metrics: BacktestMetrics } | null = null

  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'tool_call') continue
      if (p.name !== 'run_backtest') continue
      const raw = p.result
      if (typeof raw !== 'string' || !raw.trim()) continue

      let parsed: Record<string, unknown> | null = null
      try {
        const v = JSON.parse(raw)
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          parsed = v as Record<string, unknown>
        }
      } catch {
        // Tool sometimes wraps the JSON in a markdown ```json fence
        // — strip the fence and retry once.
        const fence = raw.match(/```(?:json)?\s*([\s\S]*?)```/)
        if (fence) {
          try {
            const v = JSON.parse(fence[1])
            if (v && typeof v === 'object' && !Array.isArray(v)) {
              parsed = v as Record<string, unknown>
            }
          } catch {
            /* fall through */
          }
        }
      }
      if (!parsed) continue

      const metricsRaw = parsed.metrics
      const metricsObj =
        metricsRaw && typeof metricsRaw === 'object' && !Array.isArray(metricsRaw)
          ? (metricsRaw as Record<string, unknown>)
          : {}
      const pick = (k: string): number | undefined => {
        const v = metricsObj[k]
        return typeof v === 'number' && Number.isFinite(v) ? v : undefined
      }

      const bm: BacktestMetrics = {
        total_return: pick('total_return'),
        sharpe: pick('sharpe'),
        max_drawdown: pick('max_drawdown'),
        annual_return: pick('annual_return'),
        win_rate: pick('win_rate'),
        run: typeof parsed.run === 'string' ? parsed.run : undefined,
        strategy:
          typeof parsed.strategy === 'string' ? parsed.strategy : undefined,
        timestamp: m.created_at,
      }
      if (!best || bm.timestamp > best.metrics.timestamp) {
        best = { metrics: bm }
      }
    }
  }

  return best ? best.metrics : null
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