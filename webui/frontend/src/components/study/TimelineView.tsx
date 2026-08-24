/**
 * TimelineView — View C (timeline mode).
 *
 * Left panel: agent list with status dots
 * Right panel: selected agent detail (thinking, tool calls, output)
 */

import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Clock,
  CheckCircle,
  AlertTriangle,
  Wrench,
  Sparkles,
} from 'lucide-react'
import { MarkdownRenderer } from '../chat/MarkdownRenderer'
import type { AgentTrace, ToolCallInfo } from './agentTraceTypes'

// ── Status icon ───────────────────────────────────────────────

function StatusIcon({ status }: { status: AgentTrace['status'] }) {
  if (status === 'completed') {
    return <CheckCircle className="h-3 w-3 text-emerald-400" />
  }
  if (status === 'max_iterations') {
    return <Clock className="h-3 w-3 text-amber-400" />
  }
  return <AlertTriangle className="h-3 w-3 text-red-400" />
}

// ── Tool call list (detail panel) ─────────────────────────────

function ToolCallDetail({ tc }: { tc: ToolCallInfo }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border-b border-slate-800/40 last:border-b-0">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-slate-800/30"
      >
        <Wrench className="h-3 w-3 flex-shrink-0 text-slate-500" />
        <span className="font-mono text-slate-300 truncate">{tc.tool}</span>
        <span className={`ml-auto flex-shrink-0 ${tc.status === 'error' ? 'text-red-400' : 'text-emerald-400'}`}>
          {tc.status === 'error' ? '❌' : '✅'}
        </span>
        {expanded ? (
          <ChevronDown className="h-3 w-3 flex-shrink-0 text-slate-600" />
        ) : (
          <ChevronRight className="h-3 w-3 flex-shrink-0 text-slate-600" />
        )}
      </button>
      {expanded && (
        <div className="space-y-1 border-t border-slate-800/40 bg-slate-900/50 px-2 py-2">
          <pre className="max-h-32 overflow-auto rounded bg-slate-800/60 p-2 text-[11px] text-slate-300">
            {JSON.stringify(tc.arguments, null, 2)}
          </pre>
          {tc.result !== undefined && tc.result !== '' && (
            <pre className="max-h-32 overflow-auto rounded bg-slate-800/60 p-2 text-[11px] text-slate-300">
              {typeof tc.result === 'string'
                ? tc.result.slice(0, 500)
                : JSON.stringify(tc.result, null, 2).slice(0, 500)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── Agent detail panel ────────────────────────────────────────

function AgentDetail({ trace }: { trace: AgentTrace }) {
  const [thinkingExpanded, setThinkingExpanded] = useState(false)
  const [toolsExpanded, setToolsExpanded] = useState(false)

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{trace.icon}</span>
          <span className="text-sm font-medium text-slate-200">{trace.agentName}</span>
          <StatusIcon status={trace.status} />
          <span className="text-[10px] text-slate-500">
            {trace.status === 'max_iterations' ? '超时' : trace.status === 'error' ? '错误' : '完成'}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
          <span>{trace.iterations}/{trace.maxIterations} 迭代</span>
          <span>{trace.toolCalls.length} 工具调用</span>
          {(trace.elapsedSeconds ?? 0) > 0 && <span>{trace.elapsedSeconds}s</span>}
        </div>
      </div>

      {/* Thinking */}
      {trace.thinkingBlocks.length > 0 && (
        <div className="flex-shrink-0 border-b border-slate-800">
          <button
            type="button"
            onClick={() => setThinkingExpanded(!thinkingExpanded)}
            className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs hover:bg-slate-800/20"
          >
            <Sparkles className="h-3 w-3 text-violet-400" />
            <span className="text-slate-400">
              思考过程 ({trace.thinkingBlocks.length} 轮)
            </span>
            {thinkingExpanded ? (
              <ChevronDown className="ml-auto h-3 w-3 text-slate-600" />
            ) : (
              <ChevronRight className="ml-auto h-3 w-3 text-slate-600" />
            )}
          </button>
          {thinkingExpanded && (
            <div className="max-h-60 space-y-1.5 overflow-y-auto border-t border-slate-800/40 bg-slate-900/30 px-4 py-2">
              {trace.thinkingBlocks.map((block, i) => (
                <div key={i} className="rounded bg-slate-800/40 p-2">
                  <div className="mb-1 text-[10px] text-slate-600">迭代 {block.iteration}</div>
                  <div className="text-xs text-slate-400 leading-relaxed">
                    <MarkdownRenderer content={block.text} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tool calls */}
      {trace.toolCalls.length > 0 && (
        <div className="flex-shrink-0 border-b border-slate-800">
          <button
            type="button"
            onClick={() => setToolsExpanded(!toolsExpanded)}
            className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs hover:bg-slate-800/20"
          >
            <Wrench className="h-3 w-3 text-cyan-400" />
            <span className="text-slate-400">
              工具调用 ({trace.toolCalls.length} 次)
            </span>
            {toolsExpanded ? (
              <ChevronDown className="ml-auto h-3 w-3 text-slate-600" />
            ) : (
              <ChevronRight className="ml-auto h-3 w-3 text-slate-600" />
            )}
          </button>
          {toolsExpanded && (
            <div className="max-h-60 overflow-y-auto border-t border-slate-800/40">
              {trace.toolCalls.map((tc) => (
                <ToolCallDetail key={tc.id} tc={tc} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Final output */}
      {trace.finalOutputs.length > 0 && (
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-600">
            最终输出
          </div>
          <div className="rounded-md bg-slate-800/40 p-3 text-xs text-slate-300">
            <MarkdownRenderer content={trace.finalOutputs[trace.finalOutputs.length - 1]} />
          </div>
        </div>
      )}
    </div>
  )
}

// ── Main view ─────────────────────────────────────────────────

export function TimelineView({ traces }: { traces: AgentTrace[] }) {
  const [selectedIndex, setSelectedIndex] = useState(0)

  if (traces.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-xs text-slate-500">暂无执行数据</p>
      </div>
    )
  }

  const selected = traces[selectedIndex]

  return (
    <div className="flex h-full">
      {/* Left: agent list */}
      <div className="w-52 flex-shrink-0 border-r border-slate-800 overflow-y-auto">
        <div className="px-3 py-2 text-[10px] font-medium uppercase tracking-wider text-slate-600">
          Agent 执行链
        </div>
        {traces.map((trace, i) => {
          const isActive = i === selectedIndex
          const colorMap: Record<string, string> = {
            blue: 'bg-blue-500',
            violet: 'bg-violet-500',
            emerald: 'bg-emerald-500',
            cyan: 'bg-cyan-500',
            amber: 'bg-amber-500',
            red: 'bg-red-500',
            yellow: 'bg-yellow-500',
            pink: 'bg-pink-500',
            orange: 'bg-orange-500',
          }
          const dotColor = colorMap[trace.color] || 'bg-slate-500'

          return (
            <div key={trace.agentId}>
              <button
                type="button"
                onClick={() => setSelectedIndex(i)}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${
                  isActive
                    ? 'bg-slate-800/60 text-slate-200'
                    : 'text-slate-400 hover:bg-slate-800/30'
                }`}
              >
                <div className={`h-2 w-2 rounded-full flex-shrink-0 ${dotColor}`} />
                <span className="truncate">{trace.icon} {trace.agentName}</span>
                <StatusIcon status={trace.status} />
              </button>
              {i < traces.length - 1 && (
                <div className="ml-[17px] h-3 w-px bg-slate-700" />
              )}
            </div>
          )
        })}
      </div>

      {/* Right: detail panel */}
      <div className="flex-1 min-w-0">
        <AgentDetail trace={selected} />
      </div>
    </div>
  )
}
