import { Bot } from 'lucide-react'
import type { Message, MessagePart, ToolCallPart } from '../../stores/chat'
import type { ChatLayout } from '../../stores/layout'
import { useSystemStore } from '../../stores/system'
import { getThinkingParser } from '../../utils/thinkingParsers'
import { MarkdownRenderer } from './MarkdownRenderer'
import { ToolCallBlock } from './ToolCallBlock'
import { ToolCallGroup } from './ToolCallGroup'
import { ThinkingBlock } from './ThinkingBlock'
import { FileEditBlock } from './FileEditBlock'
import { TableBlock } from './TableBlock'
import { ChartBlock } from './ChartBlock'
import { ImageBlock } from './ImageBlock'
import { StreamingText } from './StreamingText'
import { formatTime } from '../../utils/time'

interface AssistantMessageProps {
  message: Message
  isStreaming?: boolean
  streamingText?: string
  isQueued?: boolean
  layout: ChatLayout
}


/**
 * Indicator shown while an assistant message is queued behind an
 * in-flight attempt on the same session. Displays a pulsing dot and
 * "等待中... {position}/{length}" so the user knows their message was
 * accepted and is queued.
 */
function QueuedIndicator({
  queuePosition,
  queueLength,
}: {
  queuePosition?: number
  queueLength?: number
}) {
  const pos = queuePosition ?? 1
  const len = queueLength ?? pos
  return (
    <div
      className="inline-flex items-center gap-2 rounded-md border border-slate-700/60 bg-slate-800/40 px-3 py-2 text-xs text-slate-400 animate-pulse"
      aria-label={`消息排队中，第 ${pos} 位，共 ${len} 条`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
      <span>
        等待中... {pos}/{len}
      </span>
    </div>
  )
}

/**
 * Parse a text part using the active provider's thinking parser.
 * If thinking is extracted, return [thinking_part, text_part].
 * If no thinking or parser throws, return the original part.
 */
function expandTextPart(part: MessagePart, provider: string | null): MessagePart[] {
  if (part.type !== 'text') return [part]
  const text = part.text
  if (!text) return [part]

  const parser = getThinkingParser(provider)
  let parsed: { thinking: string; content: string }
  try {
    parsed = parser(text)
  } catch (err) {
    // Fail-safe: silent + console.warn, keep original text
    console.warn('[thinkingParsers] parse failed:', err)
    return [part]
  }

  if (!parsed.thinking) {
    return [part]
  }

  const result: MessagePart[] = [
    { type: 'thinking', text: parsed.thinking, collapsed: true },
  ]
  if (parsed.content) {
    // Reuse the source text part's id so the derived text part has
    // the same id (covers the persisted view + the live stream view).
    result.push({
      type: 'text',
      id: part.id,
      text: parsed.content,
    })
  }
  return result
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
  isQueued,
  layout,
}: AssistantMessageProps) {
  const provider = useSystemStore((s) => s.llm.provider)

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
      // Provider-aware thinking extraction: split text parts that contain
      // inline thinking tags into [thinking, content] parts.
      const expanded = expandTextPart(part, provider)
      for (const p of expanded) {
        groupedParts.push({ type: 'single', part: p })
      }
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
      {isQueued ? (
        <QueuedIndicator
          queuePosition={message.metadata?.queue_position}
          queueLength={message.metadata?.queue_length}
        />
      ) : isStreaming && streamingText !== undefined ? (
        <StreamingText text={streamingText} isDone={false} />
      ) : (
        groupedParts.map((item, idx) => {
          if (item.type === 'tool_group') {
            return <ToolCallGroup key={idx} toolCalls={item.calls} />
          }

          // Auto-collapse thinking when text content follows
          if (item.type === 'single' && item.part.type === 'thinking') {
            const nextItem = groupedParts[idx + 1]
            const hasTextAfter = nextItem?.type === 'single' && nextItem.part.type === 'text'
            return (
              <ThinkingBlock
                key={idx}
                text={item.part.text}
                collapsed={hasTextAfter || item.part.collapsed || isStreaming}
                streaming={isStreaming}
              />
            )
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