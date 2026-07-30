import { useRef, useEffect } from 'react'
import { Virtuoso } from 'react-virtuoso'
import type { VirtuosoHandle } from 'react-virtuoso'
import { useChatStore } from '../../stores/chat'
import { useLayoutStore } from '../../stores/layout'
import { useSessionStore } from '../../stores/session'
import { MessageBubble } from './MessageBubble'
import { AssistantMessage } from './AssistantMessage'
import { EmptyState } from '../common/EmptyState'
import { QueuePauseBanner } from './QueuePauseBanner'
import { MessageSquare } from 'lucide-react'

export function MessageList() {
  const messages = useChatStore((s) => s.messages)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  const streamingText = useChatStore((s) => s.streamingText)
  const chatLayout = useLayoutStore((s) => s.chatLayout)
  const queuePaused = useChatStore((s) => s.queuePaused)
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const virtuosoRef = useRef<VirtuosoHandle>(null)

  const messageList = Array.from(messages.values()).sort((a, b) => a.created_at - b.created_at)
  const isQueuePaused = currentSessionId
    ? queuePaused.get(currentSessionId) ?? false
    : false

  useEffect(() => {
    if (messageList.length > 0) {
      setTimeout(() => {
        virtuosoRef.current?.scrollToIndex({
          index: messageList.length - 1,
          align: 'end',
        })
      }, 50)
    }
  }, [messageList.length, streamingText])

  // Listen for global "focus chat" event (from sidebar chat icon)
  useEffect(() => {
    const handler = () => {
      virtuosoRef.current?.scrollToIndex({
        index: messageList.length - 1,
        align: 'end',
      })
    }
    window.addEventListener('sr:focus-chat', handler)
    return () => window.removeEventListener('sr:focus-chat', handler)
  }, [messageList.length])

  const banner = isQueuePaused ? <QueuePauseBanner /> : null

  if (messageList.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {banner}
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={<MessageSquare className="h-12 w-12" />}
            title="开始对话"
            description="发送消息与 Agent 交流"
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {banner}
      <Virtuoso
        ref={virtuosoRef}
        data={messageList}
        totalCount={messageList.length}
        itemContent={(_index, message) => {
          const isStreaming = message.id === streamingMessageId
          // Queued: assistant placeholder with no parts and not currently streaming.
          const isQueued =
            message.role === 'assistant' &&
            message.metadata?.queue_status === 'queued' &&
            !isStreaming
          if (message.role === 'user') {
            return <MessageBubble message={message} layout={chatLayout} />
          }
          return (
            <AssistantMessage
              message={message}
              isStreaming={isStreaming}
              streamingText={isStreaming ? streamingText : undefined}
              isQueued={isQueued}
              layout={chatLayout}
            />
          )
        }}
        followOutput="smooth"
        overscan={200}
        style={{ height: '100%' }}
      />
    </div>
  )
}