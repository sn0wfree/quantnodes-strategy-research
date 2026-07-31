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
import { ContextUsageBar } from './ContextUsageBar'
import { CompactBanner } from './CompactBanner'
import { MessageSquare } from 'lucide-react'

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

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
  const compactBanner = <CompactBanner />
  const usageBar = <ContextUsageBar />

  if (messageList.length === 0) {
    return (
      <div className="flex h-full flex-col">
        {banner}
        {compactBanner}
        {usageBar}
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
      {compactBanner}
      {usageBar}
      <Virtuoso
        ref={virtuosoRef}
        data={messageList}
        totalCount={messageList.length}
        itemContent={(_index, message) => {
          // Tool messages are persisted to DB for history reconstruction
          // (see _convert_messages_to_history in backend) but should NOT
          // render as Agent cards in the UI. Each tool result is already
          // shown inline inside the assistant message's ToolCallGroup.
          // Previously these 13+ tool records were rendered as empty Agent
          // cards, polluting the chat view.
          if (message.role === 'tool') return null

          // Error messages: show as warning bubble with collapsible detail
          if (message.message_type === 'error') {
            return (
              <div className="px-4 py-3 transition-all">
                <div className="flex gap-3">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-amber-100 text-amber-600">
                    ⚠
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex items-center gap-2 text-xs">
                      <span className="font-medium text-amber-700">请求失败</span>
                      <span className="text-slate-500">{formatTime(message.created_at)}</span>
                    </div>
                    <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                      {message.parts?.map((part, i) => (
                        <span key={i}>{part.type === 'text' ? part.text : ''}</span>
                      ))}
                      {message.metadata?.details && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-xs text-amber-700 hover:text-amber-800">
                            查看详情
                          </summary>
                          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded bg-amber-100/60 p-2 text-xs text-amber-800">
                            {message.metadata.details}
                          </pre>
                        </details>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )
          }

          // Compaction messages: show as historical summary card
          if (message.message_type === 'compaction') {
            return (
              <div className="px-4 py-3 transition-all">
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <span className="font-medium text-slate-500">📋 历史摘要</span>
                  <span className="text-slate-600">{formatTime(message.created_at)}</span>
                </div>
                <div className="text-sm text-slate-400 leading-relaxed">
                  {message.parts?.map((part, i) => (
                    <span key={i}>{part.type === 'text' ? part.text : ''}</span>
                  ))}
                </div>
              </div>
            )
          }

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