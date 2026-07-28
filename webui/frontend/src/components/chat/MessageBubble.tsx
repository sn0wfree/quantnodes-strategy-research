import type { Message } from '../../stores/chat'

interface MessageBubbleProps {
  message: Message
}

export function MessageBubble({ message }: MessageBubbleProps) {
  return (
    <div className="flex justify-end px-4 py-2">
      <div className="max-w-[70%] rounded-2xl rounded-br-md bg-primary-600 px-4 py-2.5 text-sm text-white">
        {message.parts.map((part, i) => {
          if (part.type === 'text') {
            return <span key={i} className="whitespace-pre-wrap">{part.text}</span>
          }
          if (part.type === 'image') {
            return (
              <img
                key={i}
                src={part.url}
                alt={part.alt || ''}
                className="mt-2 max-w-full rounded-lg"
              />
            )
          }
          return null
        })}
      </div>
    </div>
  )
}
