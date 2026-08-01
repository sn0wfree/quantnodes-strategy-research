import { useState, useEffect } from 'react'
import {
  CheckCircle, XCircle, Loader2, ChevronRight,
  Clock, Copy, Check, RefreshCw,
  Code, FileText, FolderOpen, Pencil, Search, Globe,
  Play, BarChart3, Database, GitCompare, TrendingDown,
  Download, Wrench, Layers, BookOpen, Calculator,
} from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'
import { MarkdownRenderer } from './MarkdownRenderer'

interface ToolCallBlockProps {
  toolCall: ToolCallPart
  startTime?: number
  onRetry?: (toolCall: ToolCallPart) => void
}

// Tool-specific icon mapping
const TOOL_ICONS: Record<string, typeof Code> = {
  write_file: Code,
  read_file: FileText,
  list_files: FolderOpen,
  edit: Pencil,
  web_search: Search,
  web_fetch: Globe,
  run_backtest: Play,
  get_market_data: BarChart3,
  import_data: Database,
  list_data_sources: Database,
  search_symbol: Search,
  factor_cross_sectional_analysis: BarChart3,
  factor_quintile_returns: BarChart3,
  factor_ic_decay: BarChart3,
  factor_turnover: BarChart3,
  strategy_compare: GitCompare,
  drawdown_analysis: TrendingDown,
  benchmark_comparison: GitCompare,
  compute_factor: Calculator,
  factor_analysis: BarChart3,
  pattern_recognition: Layers,
  options_pricing: Calculator,
  list_skills: BookOpen,
  load_skill: BookOpen,
  git_diff: GitCompare,
  list_history: Clock,
  download: Download,
}

const STATUS_CONFIG = {
  pending: { icon: Loader2, color: 'text-slate-400', bg: 'bg-slate-700/30' },
  running: { icon: Loader2, color: 'text-amber-400', bg: 'bg-amber-500/5' },
  done: { icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-500/5' },
  error: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/5' },
} as const

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Extract a brief, single-line preview from JSON args. */
function summarizeArgs(args: string | unknown): string {
  const parsed: Record<string, unknown> =
    typeof args === 'string'
      ? (() => {
          try { return JSON.parse(args) as Record<string, unknown> }
          catch { return {} }
        })()
      : ((args ?? {}) as Record<string, unknown>)
  const keys = Object.keys(parsed)
  if (keys.length === 0) {
    if (typeof args === 'string') {
      return args.length > 30 ? args.slice(0, 30) + '…' : args
    }
    return ''
  }
  return keys
    .slice(0, 2)
    .map((k) => {
      const v = parsed[k]
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return `${k}: ${s.length > 24 ? s.slice(0, 24) + '…' : s}`
    })
    .join(', ')
}

/** Smart result summary based on tool name. */
function summarizeResult(toolName: string, result: string | unknown): string {
  if (!result) return ''
  let parsed: unknown = result
  if (typeof result === 'string') {
    try { parsed = JSON.parse(result) } catch { return '' }
  }
  if (typeof parsed !== 'object' || parsed === null) return ''

  const obj = parsed as Record<string, unknown>

  // Tool-specific summaries
  if (toolName === 'get_market_data') {
    const data = obj.data as unknown[]
    if (Array.isArray(data)) return `${data.length} 条行情数据`
  }
  if (toolName === 'import_data') {
    const rows = obj.rows_inserted ?? obj.count
    if (rows !== undefined) return `已导入 ${rows} 行`
  }
  if (toolName === 'run_backtest') {
    const parts: string[] = []
    if (obj.sharpe !== undefined) parts.push(`Sharpe=${Number(obj.sharpe).toFixed(2)}`)
    if (obj.calmar !== undefined) parts.push(`Calmar=${Number(obj.calmar).toFixed(2)}`)
    if (obj.max_dd !== undefined) parts.push(`MaxDD=${Number(obj.max_dd).toFixed(2)}`)
    return parts.join(', ')
  }
  if (toolName.startsWith('factor_')) {
    const parts: string[] = []
    if (obj.ic_mean !== undefined) parts.push(`IC=${Number(obj.ic_mean).toFixed(3)}`)
    if (obj.ir !== undefined) parts.push(`IR=${Number(obj.ir).toFixed(3)}`)
    if (obj.n_rows !== undefined) parts.push(`${obj.n_rows} 行`)
    return parts.join(', ')
  }
  if (toolName === 'list_files') {
    const files = parsed as unknown[]
    if (Array.isArray(files)) return `${files.length} 个文件`
  }
  if (toolName === 'list_history') {
    const runs = obj.runs as unknown[]
    if (Array.isArray(runs)) return `${runs.length} 条实验记录`
  }
  if (toolName === 'strategy_compare') {
    const rows = obj.comparisons as unknown[]
    if (Array.isArray(rows)) return `${rows.length} 个策略对比`
  }

  // Generic fallback
  if (Array.isArray(parsed)) return `${parsed.length} 条数据`
  const keys = Object.keys(obj)
  if (keys.length > 0) return keys.slice(0, 3).join(', ')
  return ''
}

/** Render args and result as readable markdown. */
function buildMarkdown(args: string | unknown, result: string | unknown | undefined): string {
  const argsStr = typeof args === 'string' ? args : JSON.stringify(args ?? {}, null, 2)
  let md = '**Arguments**\n\n```json\n' + argsStr + '\n```\n'
  if (result !== undefined) {
    const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
    md += '\n**Result**\n\n```json\n' + resultStr + '\n```\n'
  }
  return md
}

export function ToolCallBlock({ toolCall, startTime, onRetry }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const [tickMs, setTickMs] = useState(0)

  useEffect(() => {
    if (toolCall.status !== 'running' || !startTime) return
    const id = setInterval(() => setTickMs(Date.now() - startTime), 100)
    return () => clearInterval(id)
  }, [toolCall.status, startTime])

  const config = STATUS_CONFIG[toolCall.status] ?? STATUS_CONFIG.pending
  const ToolIcon = TOOL_ICONS[toolCall.name] ?? Wrench
  const StatusIcon = config.icon
  const argsPreview = summarizeArgs(toolCall.arguments)
  const resultSummary = toolCall.status === 'done' ? summarizeResult(toolCall.name, toolCall.result) : ''
  const duration =
    toolCall.status === 'running' && startTime
      ? tickMs || Date.now() - startTime
      : startTime
      ? tickMs
      : null

  const handleCopyAll = (e: React.MouseEvent) => {
    e.stopPropagation()
    const text = buildMarkdown(toolCall.arguments, toolCall.result)
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleRetry = (e: React.MouseEvent) => {
    e.stopPropagation()
    onRetry?.(toolCall)
  }

  return (
    <div
      className={`my-1 border-l-2 rounded-r-md overflow-hidden transition-colors duration-300 ${
        toolCall.status === 'running'
          ? 'border-amber-500/50 bg-amber-500/5'
          : toolCall.status === 'error'
          ? 'border-red-500/40 bg-red-500/5'
          : toolCall.status === 'done'
          ? 'border-emerald-500/30 bg-emerald-500/5'
          : 'border-slate-700 bg-slate-800/30'
      }`}
    >
      {/* Single-line summary (always visible) */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded((v) => !v)
          }
        }}
        className="group flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-white/[0.03] transition-colors"
      >
        <ChevronRight
          className={`h-3 w-3 text-slate-500 transition-transform duration-200 ${
            expanded ? 'rotate-90' : ''
          }`}
        />
        <StatusIcon
          className={`h-3.5 w-3.5 ${config.color} ${
            toolCall.status === 'running' ? 'animate-spin' : ''
          }`}
        />
        <ToolIcon className="h-3.5 w-3.5 text-slate-500" />
        <span className="font-mono font-medium text-slate-200">
          {toolCall.name}
        </span>
        {argsPreview && (
          <span className="text-slate-500 truncate font-mono text-[11px] max-w-[260px]">
            {argsPreview}
          </span>
        )}
        {resultSummary && (
          <span className="text-emerald-400/70 truncate text-[11px] max-w-[200px]">
            → {resultSummary}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          {duration !== null && (
            <span className="flex items-center gap-0.5 font-mono">
              <Clock className="h-2.5 w-2.5" />
              {formatDuration(duration)}
            </span>
          )}
          {toolCall.status === 'error' && onRetry && (
            <button
              type="button"
              onClick={handleRetry}
              className="opacity-0 group-hover:opacity-100 transition-opacity text-red-400 hover:text-red-300"
              title="重试"
            >
              <RefreshCw className="h-3 w-3" />
            </button>
          )}
          <button
            type="button"
            onClick={handleCopyAll}
            className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-slate-300"
            title="复制 args + result"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </span>
      </div>

      {/* Progress steps (when available) */}
      {toolCall.status === 'running' && toolCall.progress && (
        <div className="border-t border-amber-500/20 px-3 py-1.5 space-y-1">
          {(toolCall.progress as string[]).map((step, idx) => (
            <div key={idx} className="flex items-center gap-1.5 text-[11px] text-amber-300/80">
              <span className="h-1 w-1 rounded-full bg-amber-400 animate-pulse" />
              {step}
            </div>
          ))}
        </div>
      )}

      {/* Expanded: markdown-rendered args + result */}
      {expanded && (
        <div className="border-t border-slate-700/40 px-3 py-2">
          <MarkdownRenderer
            content={buildMarkdown(toolCall.arguments, toolCall.result)}
          />
        </div>
      )}
    </div>
  )
}
