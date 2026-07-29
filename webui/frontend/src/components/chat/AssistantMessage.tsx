import { Bot } from 'lucide-react'
import type { Message, MessagePart, ToolCallPart } from '../../stores/chat'
import type { ChatLayout } from '../../stores/layout'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ToolCallBlock } from './ToolCallBlock'
import { ToolCallGroup } from './ToolCallGroup'
import { ThinkingBlock } from './ThinkingBlock'
import { FileEditBlock } from './FileEditBlock'
import { TableBlock } from './TableBlock'
import { ChartBlock } from './ChartBlock'
import { ImageBlock } from './ImageBlock'
import { StreamingText } from './StreamingText'

interface AssistantMessageProps {
  message: Message
  isStreaming?: boolean
  streamingText?: string
  layout: ChatLayout
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function PartRenderer({ part, isStreaming, onRetry }: { part: MessagePart; isStreaming: boolean; onRetry?: (tc: ToolCallPart) => void }) {
  switch (part.type) {
    case 'text':
      return <MarkdownRenderer content={part.text} />
    case 'tool_call':
      return <ToolCallBlock toolCall={part} onRetry={onRetry} />
    case 'thinking':
      return <ThinkingBlock text={part.text} collapsed={part.collapsed} streaming={isStreaming} />
    case 'file_edit':
      return <FileEditBlock fileEdit={part} />
    case 'table':
      return <TableBlock table={part} />
    case 'chart':
      return <ChartBlock chart={part} />
    case 'image':
      return <ImageBlock src={part.url} alt={part.alt} />
    default:
      return null
  }
}

export function AssistantMessage({
  message,
  isStreaming,
  streamingText,
  layout,
}: AssistantMessageProps) {
  const groupedParts: Array<{ type: 'single'; part: MessagePart } | { type: 'tool_group'; calls: any[] }> = []
  let i = 0
  while (i < message.parts.length) {
    const part = message.parts[i]
    if (part.type === 'tool_call') {
      const calls: any[] = []
      while (i < message.parts.length && message.parts[i].type === 'tool_call') {
        calls.push(message.parts[i])
        i++
      }
      groupedParts.push({ type: 'tool_group', calls })
    } else {
      groupedParts.push({ type: 'single', part })
      i++
    }
  }

  const modelLabel = message.metadata?.model
    ? `Agent · ${message.metadata.model}`
    : 'Agent'

  const headerLine = (
    <div className="mb-1.5 flex items-center gap-2">
      {layout === 'bubble' && (
        <span className="text-xs font-medium text-slate-400">{modelLabel}</span>
      )}
      {layout === 'flat' && (
        <>
          <span className="text-xs font-medium text-emerald-400">{modelLabel}</span>
          <span className="text-xs text-slate-600">{formatTime(message.created_at)}</span>
        </>
      )}
      {message.agent_id && (
        <span className="text-xs text-slate-600">{message.agent_id}</span>
      )}
    </div>
  )

  const body = (
    <div className="text-sm text-slate-200 space-y-3">
      {isStreaming && streamingText !== undefined ? (
        <StreamingText text={streamingText} isDone={false} />
      ) : (
        groupedParts.map((item, idx) => {
          if (item.type === 'tool_group') {
            return <ToolCallGroup key={idx} toolCalls={item.calls} />
          }
          return <PartRenderer key={idx} part={item.part} isStreaming={!!isStreaming} />
        })
      )}
    </div>
  )

  if (layout === 'flat') {
    return (
      <div className="px-4 py-3 border-b border-slate-800/40 last:border-b-0">
        {headerLine}
        {body}
      </div>
    )
  }

  // bubble mode (default)
  return (
    <div className="flex gap-3 px-4 py-2.5">
      <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-primary-600 text-white text-xs font-medium">
        <Bot className="h-3.5 w-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        {headerLine}
        {body}
      </div>
    </div>
  )
}
