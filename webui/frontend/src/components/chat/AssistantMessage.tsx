import { Bot } from 'lucide-react'
import type { Message, MessagePart } from '../../stores/chat'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ToolCallBlock } from './ToolCallBlock'
import { ThinkingBlock } from './ThinkingBlock'
import { ImageBlock } from './ImageBlock'

interface AssistantMessageProps {
  message: Message
  isStreaming?: boolean
  streamingText?: string
}

function PartRenderer({ part }: { part: MessagePart }) {
  switch (part.type) {
    case 'text':
      return <MarkdownRenderer content={part.text} />
    case 'tool_call':
      return <ToolCallBlock toolCall={part} />
    case 'thinking':
      return <ThinkingBlock text={part.text} collapsed={part.collapsed} />
    case 'file_edit':
      return (
        <div className="my-2 rounded-lg border border-slate-700/50 overflow-hidden">
          <div className="bg-slate-800 px-3 py-1.5 text-xs text-slate-400">
            {part.file_path}
          </div>
          <div className="grid grid-cols-2 text-xs">
            {part.old_content && (
              <div className="bg-red-950/30 p-2 overflow-x-auto text-red-300 font-mono">
                <pre className="whitespace-pre-wrap">{part.old_content}</pre>
              </div>
            )}
            {part.new_content && (
              <div className="bg-emerald-950/30 p-2 overflow-x-auto text-emerald-300 font-mono">
                <pre className="whitespace-pre-wrap">{part.new_content}</pre>
              </div>
            )}
          </div>
        </div>
      )
    case 'table':
      return (
        <div className="my-2 overflow-x-auto rounded-lg border border-slate-700/50">
          <table className="w-full text-sm">
            <thead className="bg-slate-800/50">
              <tr>
                {part.headers.map((h, i) => (
                  <th key={i} className="px-3 py-2 text-left font-medium text-slate-300 border-b border-slate-700/50">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {part.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-2 border-b border-slate-800/50 text-slate-300">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {part.caption && (
            <div className="px-3 py-1.5 text-xs text-slate-500 bg-slate-800/30">
              {part.caption}
            </div>
          )}
        </div>
      )
    case 'chart':
      return (
        <div className="my-2 rounded-lg border border-slate-700/50 p-4">
          <div className="text-xs text-slate-500 mb-2">{part.title || part.chart_type}</div>
          <div className="text-xs text-slate-400">图表数据: {JSON.stringify(part.data).slice(0, 100)}...</div>
        </div>
      )
    case 'image':
      return <ImageBlock src={part.url} alt={part.alt} />
    default:
      return null
  }
}

export function AssistantMessage({ message, isStreaming, streamingText }: AssistantMessageProps) {
  const agentColor = '#3b82f6' // TODO: get from agent store

  return (
    <div className="flex gap-3 px-4 py-2">
      {/* Agent avatar */}
      <div
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-white text-xs font-medium"
        style={{ backgroundColor: agentColor }}
      >
        <Bot className="h-4 w-4" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2">
          <span className="text-xs font-medium text-slate-400">Agent</span>
          {message.agent_id && (
            <span className="text-xs text-slate-600">{message.agent_id}</span>
          )}
        </div>
        <div className="text-sm text-slate-200 leading-relaxed">
          {isStreaming && streamingText !== undefined ? (
            <StreamingPart text={streamingText} />
          ) : (
            message.parts.map((part, i) => (
              <PartRenderer key={i} part={part} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function StreamingPart({ text }: { text: string }) {
  return (
    <div className="relative">
      <MarkdownRenderer content={text} />
      {!text && (
        <span className="inline-block w-2 h-4 bg-primary-400 animate-pulse ml-0.5 align-middle" />
      )}
    </div>
  )
}
