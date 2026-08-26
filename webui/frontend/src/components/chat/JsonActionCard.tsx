/**
 * JsonActionCard — renders a structured JSON action object (from a study
 * agent) in an academic-style card.  The primary content is the `action`
 * label and `hypothesis` field; other fields are collapsed behind a toggle.
 *
 * Design language: serif hints, muted palette, restrained colour —
 * academic paper / lab-notebook aesthetic.
 */
import { useState } from 'react'

interface JsonActionCardProps {
  action: string
  hypothesis?: string
  fullJson: Record<string, unknown>
}

const ACTION_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  optimize_param:  { bg: 'bg-sky-900/30',    text: 'text-sky-300',     label: '参数优化' },
  blocker:         { bg: 'bg-amber-900/30',   text: 'text-amber-300',   label: '阻塞' },
  keep:            { bg: 'bg-emerald-900/30', text: 'text-emerald-300', label: '保留' },
  discard:         { bg: 'bg-rose-900/30',    text: 'text-rose-300',    label: '丢弃' },
  report_progress: { bg: 'bg-indigo-900/30',  text: 'text-indigo-300',  label: '进度报告' },
  request_data:    { bg: 'bg-cyan-900/30',    text: 'text-cyan-300',    label: '数据请求' },
}

const DEFAULT_STYLE = { bg: 'bg-slate-800/40', text: 'text-slate-300', label: '' }

export function JsonActionCard({ action, hypothesis, fullJson }: JsonActionCardProps) {
  const [expanded, setExpanded] = useState(false)
  const style = ACTION_STYLES[action] ?? DEFAULT_STYLE
  const extraFields = Object.keys(fullJson).filter((k) => k !== 'action' && k !== 'hypothesis')

  return (
    <div className="my-1 rounded border border-slate-700/50 bg-slate-900/40 text-[12px] leading-relaxed overflow-hidden">
      {/* Header — action badge */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-700/30">
        <span
          className={`inline-block rounded px-2 py-0.5 font-mono font-medium text-[11px] ${style.bg} ${style.text}`}
        >
          {action}
        </span>
        {style.label && (
          <span className="text-[11px] text-slate-500">{style.label}</span>
        )}
      </div>

      {/* Hypothesis — the core reasoning output */}
      {hypothesis && (
        <div className="px-3 py-2 text-slate-200 whitespace-pre-wrap">
          {hypothesis}
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

/**
 * Parse a text part's content into a structured action.
 * Returns { isAction, action, hypothesis, fullJson } or { isAction: false }.
 */
export function parseJsonAction(text: string): {
  isAction: boolean
  action?: string
  hypothesis?: string
  fullJson?: Record<string, unknown>
} {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return { isAction: false }
  }
  try {
    const parsed = JSON.parse(trimmed)
    if (typeof parsed === 'object' && parsed !== null && 'action' in parsed) {
      return {
        isAction: true,
        action: parsed.action as string,
        hypothesis: typeof parsed.hypothesis === 'string' ? parsed.hypothesis : undefined,
        fullJson: parsed,
      }
    }
  } catch {
    // not valid JSON
  }
  return { isAction: false }
}
