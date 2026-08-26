/**
 * JsonActionCard — renders a structured JSON object (from a study agent) in
 * an academic-style card.  Supports two modes:
 *   1. Action mode — JSON has an `action` field → styled badge per action type
 *   2. Generic mode — JSON without `action` → neutral "JSON 输出" badge
 *
 * The primary field is `hypothesis` (highlighted) when present, otherwise
 * the first short string field is shown.  All other fields are collapsed
 * behind a toggle.
 *
 * Design language: serif hints, muted palette, restrained colour —
 * academic paper / lab-notebook aesthetic.
 */
import { useState } from 'react'

export interface JsonActionCardProps {
  /** The action type if the JSON contains an "action" field, else undefined. */
  action?: string
  /** The hypothesis or main explanatory field. */
  hypothesis?: string
  /** The full parsed JSON object. */
  fullJson: Record<string, unknown>
}

const ACTION_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  optimize_param:  { bg: 'bg-sky-900/30',    text: 'text-sky-300',     label: '参数优化' },
  blocker:         { bg: 'bg-amber-900/30',   text: 'text-amber-300',   label: '阻塞' },
  keep:            { bg: 'bg-emerald-900/30', text: 'text-emerald-300', label: '保留' },
  discard:         { bg: 'bg-rose-900/30',    text: 'text-rose-300',    label: '丢弃' },
  report_progress: { bg: 'bg-indigo-900/30',  text: 'text-indigo-300',  label: '进度报告' },
  request_data:    { bg: 'bg-cyan-900/30',    text: 'text-cyan-300',    label: '数据请求' },
  risk_assessment: { bg: 'bg-orange-900/30',  text: 'text-orange-300',  label: '风险评估' },
  portfolio:       { bg: 'bg-teal-900/30',    text: 'text-teal-300',    label: '组合配置' },
  overfit:         { bg: 'bg-violet-900/30',  text: 'text-violet-300',  label: '过拟合检查' },
}

const GENERIC_STYLE = { bg: 'bg-slate-800/60', text: 'text-slate-300', label: 'JSON 输出' }

/** Fields used as "hypothesis"-equivalent (highlighted above the field list). */
const HYPOTHESIS_LIKE = ['hypothesis', 'interpretation', 'summary', 'description', 'reason', 'message']

function pickHypothesis(json: Record<string, unknown>): string | undefined {
  for (const key of HYPOTHESIS_LIKE) {
    const v = json[key]
    if (typeof v === 'string' && v.trim().length > 0) return v
  }
  return undefined
}

export function JsonActionCard({ action, hypothesis, fullJson }: JsonActionCardProps) {
  const [expanded, setExpanded] = useState(false)
  const isActionMode = !!action && action in ACTION_STYLES
  const style = isActionMode
    ? ACTION_STYLES[action!]
    : (action ? { bg: 'bg-slate-700/40', text: 'text-slate-300', label: action } : GENERIC_STYLE)
  const hyp = hypothesis ?? (isActionMode ? undefined : pickHypothesis(fullJson))
  const skipKeys = new Set(['action', 'hypothesis', ...HYPOTHESIS_LIKE])
  const extraFields = Object.keys(fullJson).filter((k) => !skipKeys.has(k))

  return (
    <div className="my-1 rounded border border-slate-700/50 bg-slate-900/40 text-[12px] leading-relaxed overflow-hidden">
      {/* Header — badge */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-700/30">
        <span
          className={`inline-block rounded px-2 py-0.5 font-mono font-medium text-[11px] ${style.bg} ${style.text}`}
        >
          {action ?? 'json'}
        </span>
        {style.label && (
          <span className="text-[11px] text-slate-500">{style.label}</span>
        )}
      </div>

      {/* Hypothesis / highlighted field */}
      {hyp && (
        <div className="px-3 py-2 text-slate-200 whitespace-pre-wrap">
          {hyp}
        </div>
      )}

      {/* Other fields — collapsible */}
      {extraFields.length > 0 && (
        <div className="border-t border-slate-700/30">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-between px-3 py-1.5 text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
          >
              <span>其他字段 ({extraFields.length})</span>
              <span className="text-[10px]">{expanded ? '▼' : '▶'}</span>
            </button>
            {expanded && (
              <div className="px-3 pb-2">
                <pre className="text-[11px] text-slate-400 font-mono whitespace-pre-wrap break-words">
                  {JSON.stringify(
                    Object.fromEntries(extraFields.map((k) => [k, fullJson[k]])),
                    null,
                    2,
                  )}
                </pre>
              </div>
            )}
        </div>
      )}
    </div>
  )
}

// ── Content parser ──────────────────────────────────────────────

export interface ParsedSegment {
  kind: 'text' | 'json'
  /** For kind=text: markdown text. */
  text?: string
  /** For kind=json: parsed object + optional action key. */
  json?: Record<string, unknown>
  action?: string
}

export interface ParseResult {
  segments: ParsedSegment[]
  /** True if any segment is a JSON card (action / generic). */
  hasStructured: boolean
}

/**
 * Parse a text part into an ordered list of markdown text segments and
 * JSON object segments.  Handles three input shapes:
 *   1. Pure markdown — single text segment
 *   2. Pure JSON object — single JSON segment (action or generic)
 *   3. Markdown with embedded JSON — multiple segments interleaved
 *
 * Returns { segments, hasStructured }.  Malformed JSON is treated as plain
 * text (no crash, no card).
 */
export function parseStructuredContent(rawText: string): ParseResult {
  const text = rawText.trim()
  if (!text) return { segments: [], hasStructured: false }

  // Fast path: pure JSON object (starts with { ends with })
  if (text.startsWith('{') && text.endsWith('}')) {
    const obj = tryParseJson(text)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const jsonObj = obj as Record<string, unknown>
      return {
        segments: [{
          kind: 'json',
          json: jsonObj,
          action: typeof jsonObj.action === 'string' ? jsonObj.action : undefined,
        }],
        hasStructured: true,
      }
    }
  }

  // Mixed / markdown: scan top-level JSON blocks via bracket pairing.
  const segments: ParsedSegment[] = []
  let cursor = 0
  let foundAnyJson = false

  while (cursor < text.length) {
    const startIdx = text.indexOf('{', cursor)
    if (startIdx === -1) {
      // No more JSON — push remaining as text
      segments.push({ kind: 'text', text: text.slice(cursor) })
      break
    }
    // Push any markdown before the JSON
    if (startIdx > cursor) {
      segments.push({ kind: 'text', text: text.slice(cursor, startIdx) })
    }
    // Find balanced end
    const endIdx = findBalancedJsonEnd(text, startIdx)
    if (endIdx === -1) {
      // Unbalanced — treat remaining as text
      segments.push({ kind: 'text', text: text.slice(startIdx) })
      break
    }
    const candidate = text.slice(startIdx, endIdx + 1)
    const obj = tryParseJson(candidate)
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const jsonObj = obj as Record<string, unknown>
      segments.push({
        kind: 'json',
        json: jsonObj,
        action: typeof jsonObj.action === 'string' ? jsonObj.action : undefined,
      })
      foundAnyJson = true
      cursor = endIdx + 1
    } else {
      // Malformed JSON — skip just this character, treat rest as text
      cursor = startIdx + 1
    }
  }

  // Drop empty text segments (whitespace between JSON blocks).
  return {
    segments: segments.filter(
      (s) => s.kind === 'json' || (s.text && s.text.trim().length > 0),
    ),
    hasStructured: foundAnyJson,
  }
}

function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}

/**
 * Given text and a start index pointing at '{', find the index of the
 * matching '}' via depth counting (respecting strings).  Returns -1 if
 * unbalanced.
 */
function findBalancedJsonEnd(text: string, startIdx: number): number {
  let depth = 0
  let i = startIdx
  let inString = false
  let escape = false
  while (i < text.length) {
    const ch = text[i]
    if (inString) {
      if (escape) {
        escape = false
      } else if (ch === '\\') {
        escape = true
      } else if (ch === '"') {
        inString = false
      }
    } else {
      if (ch === '"') {
        inString = true
      } else if (ch === '{') {
        depth += 1
      } else if (ch === '}') {
        depth -= 1
        if (depth === 0) return i
      }
    }
    i += 1
  }
  return -1
}