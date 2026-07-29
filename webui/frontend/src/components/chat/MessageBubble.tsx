import type { Message, MessagePart } from '../../stores/chat'
import type { ChatLayout } from '../../stores/layout'

interface MessageBubbleProps {
  message: Message
  layout: ChatLayout
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function PartContent({ part }: { part: MessagePart }) {
  if (part.type === 'text') {
    return <span className="whitespace-pre-wrap">{part.text}</span>
  }
  if (part.type === 'image') {
    return (
      <img
        src={part.url}
        alt={part.alt || ''}
        className="mt-2 max-w-full rounded-lg"
      />
    )
  }
  return null
}

export function MessageBubble({ message, layout }: MessageBubbleProps) {
  if (layout === 'flat') {
    return (
      <div className="px-4 py-3">
        <div className="mb-1 flex items-center gap-2 text-xs">
          <span className="font-medium text-primary-400">You</span>
          <span className="text-slate-600">{formatTime(message.created_at)}</span>
        </div>
        <div className="text-sm text-slate-200 leading-relaxed">
          {message.parts.map((part, i) => (
            <PartContent key={i} part={part} />
          ))}
        </div>
      </div>
    )
  }

  // bubble mode
  return (
    <div className="flex justify-end px-4 py-2">
      <div className="max-w-[70%] rounded-2xl rounded-br-md bg-primary-600 px-4 py-2.5 text-sm text-white">
        {message.parts.map((part, i) => (
          <PartContent key={i} part={part} />
        ))}
      </div>
    </div>
  )
}