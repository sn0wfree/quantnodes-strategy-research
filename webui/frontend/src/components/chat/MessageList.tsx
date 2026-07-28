import { useRef, useEffect } from 'react'
import { Virtuoso } from 'react-virtuoso'
import type { VirtuosoHandle } from 'react-virtuoso'
import { useChatStore } from '../../stores/chat'
import { MessageBubble } from './MessageBubble'
import { AssistantMessage } from './AssistantMessage'
import { EmptyState } from '../common/EmptyState'
import { MessageSquare } from 'lucide-react'

export function MessageList() {
  const messages = useChatStore((s) => s.messages)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  const streamingText = useChatStore((s) => s.streamingText)
  const virtuosoRef = useRef<VirtuosoHandle>(null)

  const messageList = Array.from(messages.values()).sort((a, b) => a.created_at - b.created_at)

  // Auto-scroll to bottom on new messages
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

  if (messageList.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={<MessageSquare className="h-12 w-12" />}
          title="开始对话"
          description="发送消息与 Agent 交流"
        />
      </div>
    )
  }

  return (
    <Virtuoso
      ref={virtuosoRef}
      data={messageList}
      totalCount={messageList.length}
      itemContent={(_index, message) => {
        const isStreaming = message.id === streamingMessageId
        if (message.role === 'user') {
          return <MessageBubble message={message} />
        }
        return (
          <AssistantMessage
            message={message}
            isStreaming={isStreaming}
            streamingText={isStreaming ? streamingText : undefined}
          />
        )
      }}
      followOutput="smooth"
      overscan={200}
      style={{ height: '100%' }}
    />
  )
}
