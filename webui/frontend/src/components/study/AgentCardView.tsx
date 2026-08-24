/**
 * AgentCardView — View A (card mode).
 *
 * Each agent renders as a collapsible card showing:
 * - Header: icon + name + status + stats
 * - Thinking blocks (collapsed by default)
 * - Tool calls (collapsed by default)
 * - Final output
 */

import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle,
  AlertTriangle,
  Loader2,
  Wrench,
  Sparkles,
} from 'lucide-react'
import { MarkdownRenderer } from '../chat/MarkdownRenderer'
import type { AgentTrace, ToolCallInfo, ThinkingBlock } from './agentTraceTypes'

// ── Status badge ──────────────────────────────────────────────

function StatusBadge({ trace }: { trace: AgentTrace }) {
  if (trace.status === 'completed') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
        <CheckCircle className="h-3 w-3" /> 完成
      </span>
    )
  }
  if (trace.status === 'max_iterations') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400">
        <Clock className="h-3 w-3" /> 超时
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-400">
      <AlertTriangle className="h-3 w-3" /> 错误
    </span>
  )
}

// ── Tool call item ────────────────────────────────────────────

function ToolCallItem({ tc }: { tc: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false)
  const isError = tc.status === 'error'

  return (
    <div className="border-b border-slate-800/40 last:border-b-0">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-800/30"
      >
        <Wrench className="h-3 w-3 flex-shrink-0 text-slate-500" />
        <span className="font-mono text-slate-300">{tc.tool}</span>
        <span className={`ml-auto flex-shrink-0 ${isError ? 'text-red-400' : 'text-emerald-400'}`}>
          {isError ? '❌' : '✅'}
        </span>
        {expanded ? (
          <ChevronDown className="h-3 w-3 flex-shrink-0 text-slate-600" />
        ) : (
          <ChevronRight className="h-3 w-3 flex-shrink-0 text-slate-600" />
        )}
      </button>
      {expanded && (
        <div className="space-y-1 border-t border-slate-800/40 bg-slate-900/50 px-3 py-2">
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-600">
              参数
            </div>
            <pre className="max-h-40 overflow-auto rounded bg-slate-800/60 p-2 text-[11px] text-slate-300">
              {JSON.stringify(tc.arguments, null, 2)}
            </pre>
          </div>
          {tc.result !== undefined && tc.result !== '' && (
            <div>
              <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-600">
                结果
              </div>
              <pre className="max-h-40 overflow-auto rounded bg-slate-800/60 p-2 text-[11px] text-slate-300">
                {typeof tc.result === 'string'
                  ? tc.result.slice(0, 1000)
                  : JSON.stringify(tc.result, null, 2).slice(0, 1000)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Thinking section ──────────────────────────────────────────

function ThinkingSection({ blocks }: { blocks: ThinkingBlock[] }) {
  const [expanded, setExpanded] = useState(false)
  if (blocks.length === 0) return null

  const totalChars = blocks.reduce((s, b) => s + b.text.length, 0)

  return (
    <div className="border-t border-slate-800/40">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-800/20"
      >
        <Sparkles className="h-3 w-3 flex-shrink-0 text-violet-400" />
        <span className="text-slate-400">
          思考过程 ({blocks.length} 轮, {totalChars} 字符)
        </span>
        {expanded ? (
          <ChevronDown className="ml-auto h-3 w-3 text-slate-600" />
        ) : (
          <ChevronRight className="ml-auto h-3 w-3 text-slate-600" />
        )}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-slate-800/40 bg-slate-900/30 px-3 py-2">
          {blocks.map((block, i) => (
            <div key={i} className="rounded-md bg-slate-800/40 p-2">
              <div className="mb-1 text-[10px] font-medium text-slate-600">
                迭代 {block.iteration}
              </div>
              <div className="text-xs text-slate-400 leading-relaxed">
                <MarkdownRenderer content={block.text} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Tool calls section ────────────────────────────────────────

function ToolCallsSection({ toolCalls }: { toolCalls: ToolCallInfo[] }) {
  const [expanded, setExpanded] = useState(false)
  if (toolCalls.length === 0) return null

  const errorCount = toolCalls.filter(tc => tc.status === 'error').length

  return (
    <div className="border-t border-slate-800/40">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-slate-800/20"
      >
        <Wrench className="h-3 w-3 flex-shrink-0 text-cyan-400" />
        <span className="text-slate-400">
          工具调用 ({toolCalls.length} 次
          {errorCount > 0 && <>, <span className="text-red-400">{errorCount} 失败</span></>}
          )
        </span>
        {expanded ? (
          <ChevronDown className="ml-auto h-3 w-3 text-slate-600" />
        ) : (
          <ChevronRight className="ml-auto h-3 w-3 text-slate-600" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-slate-800/40">
          {toolCalls.map((tc) => (
            <ToolCallItem key={tc.id} tc={tc} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Single agent card ─────────────────────────────────────────

function AgentCard({ trace }: { trace: AgentTrace }) {
  const colorMap: Record<string, string> = {
    blue: 'border-blue-500/30',
    violet: 'border-violet-500/30',
    emerald: 'border-emerald-500/30',
    cyan: 'border-cyan-500/30',
    amber: 'border-amber-500/30',
    red: 'border-red-500/30',
    yellow: 'border-yellow-500/30',
    pink: 'border-pink-500/30',
    orange: 'border-orange-500/30',
  }
  const borderColor = colorMap[trace.color] || 'border-slate-700'

  return (
    <div className={`rounded-lg border ${borderColor} bg-slate-900/60 overflow-hidden`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5">
        <span className="text-lg">{trace.icon}</span>
        <span className="text-sm font-medium text-slate-200">{trace.agentName}</span>
        <StatusBadge trace={trace} />
        <span className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          <span>{trace.iterations}/{trace.maxIterations} 迭代</span>
          <span>·</span>
          <span>{trace.toolCalls.length} 工具</span>
          {(trace.elapsedSeconds ?? 0) > 0 && (
            <>
              <span>·</span>
              <span>{trace.elapsedSeconds}s</span>
            </>
          )}
        </span>
      </div>

      {/* Thinking blocks */}
      <ThinkingSection blocks={trace.thinkingBlocks} />

      {/* Tool calls */}
      <ToolCallsSection toolCalls={trace.toolCalls} />

      {/* Final output */}
      {trace.finalOutputs.length > 0 && (
        <div className="border-t border-slate-800/40 px-3 py-2.5">
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-600">
            最终输出
          </div>
          <div className="max-h-60 overflow-auto rounded-md bg-slate-800/40 p-2.5 text-xs text-slate-300">
            <MarkdownRenderer content={trace.finalOutputs[trace.finalOutputs.length - 1]} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main view ─────────────────────────────────────────────────

export function AgentCardView({ traces }: { traces: AgentTrace[] }) {
  if (traces.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-slate-500" />
          <p className="mt-2 text-xs text-slate-500">加载执行数据...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3 overflow-y-auto p-3">
      {traces.map((trace) => (
        <AgentCard key={trace.agentId} trace={trace} />
      ))}
    </div>
  )
}
