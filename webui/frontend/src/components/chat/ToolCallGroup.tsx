import { useState } from 'react'
import { ChevronDown, ChevronRight, Wrench } from 'lucide-react'
import type { ToolCallPart } from '../../stores/chat'
import { ToolCallBlock } from './ToolCallBlock'

interface ToolCallGroupProps {
  toolCalls: ToolCallPart[]
}

export function ToolCallGroup({ toolCalls }: ToolCallGroupProps) {
  const [expanded, setExpanded] = useState(false)

  if (toolCalls.length === 0) return null
  if (toolCalls.length === 1) {
    return <ToolCallBlock toolCall={toolCalls[0]} />
  }

  const running = toolCalls.filter((tc) => tc.status === 'running').length
  const done = toolCalls.filter((tc) => tc.status === 'done').length
  const error = toolCalls.filter((tc) => tc.status === 'error').length

  return (
    <div className="my-1.5 rounded-lg border border-slate-700/50 overflow-hidden">
      {/* Group header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 bg-slate-800/30 px-3 py-2 text-left text-xs hover:bg-slate-800/50 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-slate-500" />
        ) : (
          <ChevronRight className="h-3 w-3 text-slate-500" />
        )}
        <Wrench className="h-3.5 w-3.5 text-slate-400" />
        <span className="text-slate-300">
          {toolCalls.length} 个工具调用
        </span>
        <div className="ml-auto flex items-center gap-2 text-[10px]">
          {running > 0 && (
            <span className="flex items-center gap-1 text-amber-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
              {running} 运行中
            </span>
          )}
          {done > 0 && (
            <span className="text-emerald-400">{done} 完成</span>
          )}
          {error > 0 && (
            <span className="text-red-400">{error} 失败</span>
          )}
        </div>
      </button>

      {/* Expanded: individual tool calls */}
      {expanded && (
        <div className="border-t border-slate-700/50 p-1 space-y-1">
          {toolCalls.map((tc) => (
            <ToolCallBlock key={tc.id} toolCall={tc} />
          ))}
        </div>
      )}
    </div>
  )
}
