import type { Message } from '../stores/chat'

export interface FileChange {
  path: string
  status: 'modified' | 'created' | 'deleted'
  timestamp: number
}

export interface ToolActivity {
  id: string
  name: string
  status: 'pending' | 'running' | 'done' | 'error'
  preview: string
  timestamp: number
}

export interface BacktestResult {
  title: string
  metrics: { label: string; value: string }[]
  chartType: string
  timestamp: number
}

export interface StrategyFile {
  path: string
  status: 'modified' | 'created'
  old_content: string
  new_content: string
  timestamp: number
}

/**
 * Tunable parameters for context-panel extractors. Centralised so
 * heuristics can be tweaked without hunting through component code.
 *
 * TODO: dive deeper into each rule set:
 *   - file-edit diffing: detect create vs modify vs delete from
 *     old_content / new_content (currently no-op; status is always
 *     'modified').
 *   - tool preview: pick the most informative field from arguments vs
 *     result based on tool name (currently truncates arguments).
 *   - backtest detection: title/regex matching to distinguish real
 *     backtest charts from generic line/bar plots.
 *   - dedupe windows: how long to suppress repeat events for the same
 *     target.
 */
export interface ExtractorConfig {
  /** Max number of tool rows to surface in the context panel. */
  toolLimit: number
  /** Max characters per tool preview line. */
  toolPreviewMaxChars: number
  /** Substrings (case-insensitive) that mark a chart/table as a backtest. */
  backtestTitlePatterns: string[]
  /** Regex applied to chart/table titles to extract metric label hints. */
  metricLabelHints: RegExp[]
}

export const DEFAULT_EXTRACTOR_CONFIG: ExtractorConfig = {
  toolLimit: 8,
  toolPreviewMaxChars: 60,
  // TODO: widen this list as more backtest tools are introduced.
  backtestTitlePatterns: ['backtest', '回测', '净值', 'nav', 'pnl', 'equity'],
  // TODO: replace with structured metric extraction once the backend
  // emits typed results; for now these hints are best-effort.
  metricLabelHints: [
    /\b(sharpe|sortino|calmar)\b/i,
    /\b(return|pnl|annual[_-]?return)\b/i,
    /\b(drawdown|mdd|max[_-]?dd)\b/i,
    /\b(volatility|std|vol)\b/i,
  ],
}

/**
 * Collect file edits from a message list. Same path edited twice keeps
 * the latest entry's timestamp and status.
 *
 * TODO: infer status from old/new content (create = empty old, delete
 * = empty new) once we settle on the backend schema.
 */
export function extractFileChanges(
  messages: Message[],
  _config: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG
): FileChange[] {
  const map = new Map<string, FileChange>()
  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'file_edit') continue
      // TODO: status inference (old_content === '' → 'created',
      //       new_content === '' → 'deleted', else 'modified').
      map.set(p.file_path, {
        path: p.file_path,
        status: 'modified',
        timestamp: m.created_at,
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.timestamp - a.timestamp)
}

/**
 * Latest tool calls across all messages, capped by config.toolLimit.
 * Preview string is a trimmed view of the tool arguments (preferred
 * for in-flight calls) or the result string (preferred once done).
 *
 * TODO: per-tool preview formatter (each tool's argument shape is
 * different; today we just truncate JSON / strings).
 */
export function extractToolActivity(
  messages: Message[],
  config: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG
): ToolActivity[] {
  const out: ToolActivity[] = []
  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'tool_call') continue
      const argsStr = typeof p.arguments === 'string'
        ? p.arguments
        : p.arguments ? JSON.stringify(p.arguments) : ''
      const resultStr = typeof p.result === 'string'
        ? p.result
        : p.result ? JSON.stringify(p.result) : ''
      // Prefer result when the tool is done; otherwise show the args.
      const raw = p.status === 'done' || p.status === 'error' ? resultStr || argsStr : argsStr
      const preview = raw.length > config.toolPreviewMaxChars
        ? raw.slice(0, config.toolPreviewMaxChars) + '…'
        : raw
      out.push({
        id: p.id,
        name: p.name,
        status: p.status,
        preview,
        timestamp: m.created_at,
      })
    }
  }
  out.sort((a, b) => b.timestamp - a.timestamp)
  return out.slice(0, config.toolLimit)
}

/**
 * Charts/tables whose title looks like a backtest result. Returns
 * lightweight BacktestResult objects — actual metric values aren't
 * fully decoded yet (the chart `data` payload is untyped).
 *
 * TODO: replace title-regex matching with a typed result from the
 * backtest tool once that schema lands.
 */
export function extractBacktestResults(
  messages: Message[],
  config: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG
): BacktestResult[] {
  const out: BacktestResult[] = []
  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'chart' && p.type !== 'table') continue
      // TODO: table parts carry `caption` (not `title`) — consider
      //       extending TablePart with an optional title field, or
      //       matching against the first row's labels instead.
      const rawTitle = p.type === 'chart' ? p.title : p.caption
      const title = (rawTitle ?? '').toLowerCase()
      const matches = config.backtestTitlePatterns.some(pat => title.includes(pat.toLowerCase()))
      if (!matches) continue
      const metrics: { label: string; value: string }[] = []
      if (p.type === 'table') {
        // First column is treated as the metric label, first data row
        // as the value. Best-effort until we get structured payloads.
        const headerRow = p.headers[0] ?? 'metric'
        const firstData = p.rows[0]?.[0]
        metrics.push({ label: headerRow, value: firstData != null ? String(firstData) : '—' })
      } else {
        metrics.push({ label: p.chart_type, value: `${Array.isArray(p.data) ? p.data.length : 0} pts` })
      }
      out.push({
        title: rawTitle ?? 'Backtest',
        metrics,
        chartType: p.type === 'chart' ? p.chart_type : 'table',
        timestamp: m.created_at,
      })
    }
  }
  return out.sort((a, b) => b.timestamp - a.timestamp)
}

/**
 * Collect strategy-file edits (strategy.py / config.yaml under
 * strategies/) from a message list. Same path edited twice keeps the
 * latest new_content; status is inferred from old_content (empty →
 * 'created', else 'modified').
 *
 * This powers the ContextPanel's StrategyFileSection, letting users
 * verify the actual file contents the agent wrote (truthfulness L3).
 */
export function extractStrategyFiles(messages: Message[]): StrategyFile[] {
  const map = new Map<string, StrategyFile>()
  for (const m of messages) {
    for (const p of m.parts) {
      if (p.type !== 'file_edit') continue
      if (!p.file_path.includes('strategies/')) continue
      map.set(p.file_path, {
        path: p.file_path,
        status: p.old_content === '' ? 'created' : 'modified',
        old_content: p.old_content,
        new_content: p.new_content,
        timestamp: m.created_at,
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.timestamp - a.timestamp)
}