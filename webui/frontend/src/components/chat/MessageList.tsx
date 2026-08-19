import { useRef, useEffect, useMemo } from 'react'
import { Virtuoso } from 'react-virtuoso'
import type { VirtuosoHandle } from 'react-virtuoso'
import { useChatStore, type Message } from '../../stores/chat'
import { useLayoutStore } from '../../stores/layout'
import { useChatSessionId } from '../../contexts/ChatSessionContext'
import { MessageBubble } from './MessageBubble'
import { AssistantMessage } from './AssistantMessage'
import { GoalMessage } from './GoalMessage'
import { EmptyState } from '../common/EmptyState'
import { QueuePauseBanner } from './QueuePauseBanner'
import { ContextUsageBar } from './ContextUsageBar'
import { CompactBanner } from './CompactBanner'
import { QuickStartChips } from './QuickStartChips'
import { MessageSquare } from 'lucide-react'
import { formatTime, dayLabel } from '../../utils/time'

// ── Separator support ─────────────────────────────────────────

interface SeparatorItem {
  _type: 'separator'
  _key: string
  round: number
}

type ListItem = Message | SeparatorItem

function isSeparator(item: ListItem): item is SeparatorItem {
  return (item as SeparatorItem)._type === 'separator'
}

/**
 * Inject round separator items into a sorted message list.
 * Inserts a separator before the first message of each new round.
 */
function injectSeparators(messages: Message[], keyFn: (m: Message) => string | null): ListItem[] {
  const items: ListItem[] = []
  let lastKey: string | null = null
  for (const msg of messages) {
    const key = keyFn(msg)
    if (key != null && key !== lastKey) {
      items.push({ _type: 'separator', _key: `sep-${key}`, round: parseInt(key.replace(/\D/g, ''), 10) || 0 })
      lastKey = key
    }
    items.push(msg)
  }
  return items
}

// ── Round separator component ─────────────────────────────────

function RoundSeparator({ round }: { round: number }) {
  return (
    <div className="flex items-center gap-3 px-4 pt-4 pb-1">
      <div className="h-px flex-1 bg-slate-700/60" />
      <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">
        Round {round}
      </span>
      <div className="h-px flex-1 bg-slate-700/60" />
    </div>
  )
}

export interface MessageListProps {
  /**
   * When provided, round separator dividers are injected between messages
   * whose key changes. The key is derived from the message metadata
   * (e.g. metadata.round). Messages with a null key get no separator.
   */
  separatorKey?: (msg: Message) => string | null
}


export function MessageList({ separatorKey }: MessageListProps = {}) {
  const messages = useChatStore((s) => s.messages)
  const streamingMessageId = useChatStore((s) => s.streamingMessageId)
  const chatLayout = useLayoutStore((s) => s.chatLayout)
  const queuePaused = useChatStore((s) => s.queuePaused)
  const hasMore = useChatStore((s) => s.hasMore)
  const loadMoreMessages = useChatStore((s) => s.loadMoreMessages)
  const currentSessionId = useChatSessionId()
  const virtuosoRef = useRef<VirtuosoHandle>(null)

  const rawMessages = Array.from(messages.values())
    .filter((m) => !currentSessionId || m.session_id === currentSessionId)
    .sort((a, b) => a.created_at - b.created_at)

  const messageList: ListItem[] = useMemo(
    () => separatorKey ? injectSeparators(rawMessages, separatorKey) : rawMessages,
    [rawMessages, separatorKey],
  )

  const isQueuePaused = currentSessionId
    ? queuePaused.get(currentSessionId) ?? false
    : false
  const showLoadMore = currentSessionId
    ? hasMore.get(currentSessionId) ?? false
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
  }, [messageList.length])

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
        <div className="flex flex-col items-center pb-6">
          <QuickStartChips />
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
        components={{
          Header: showLoadMore
            ? () => (
                <div className="flex justify-center py-2">
                  <button
                    onClick={() => currentSessionId && loadMoreMessages(currentSessionId)}
                    className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
                  >
                    ↑ 加载更早消息
                  </button>
                </div>
              )
            : undefined,
        }}
        itemContent={(_index, item) => {
          // Round separator items
          if (isSeparator(item)) {
            return <RoundSeparator round={item.round} />
          }

          const message = item as Message

          // Day separator: show a date divider when the calendar day
          // changes from the previous message.
          const prev = _index > 0 ? messageList[_index - 1] : null
          const showDay =
            _index === 0 ||
            (prev && !isSeparator(prev) && dayLabel((prev as Message).created_at) !== dayLabel(message.created_at))
          const daySeparator = showDay ? (
            <div className="flex items-center gap-3 px-4 pt-3">
              <div className="h-px flex-1 bg-slate-800/60" />
              <span className="text-[11px] text-slate-500">{dayLabel(message.created_at)}</span>
              <div className="h-px flex-1 bg-slate-800/60" />
            </div>
          ) : null

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
              <>
                {daySeparator}
              <div className="px-4 py-3 transition-all">
                <div className="flex gap-3">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-red-500/10 text-red-400">
                    ⚠
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex items-center gap-2 text-xs">
                      <span className="font-medium text-red-400">请求失败</span>
                      <span className="text-slate-500">{formatTime(message.created_at)}</span>
                    </div>
                    <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                      {message.parts?.map((part, i) => (
                        <span key={i}>{part.type === 'text' ? part.text : ''}</span>
                      ))}
                      {message.metadata?.details && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-xs text-red-400 hover:text-red-300">
                            查看详情
                          </summary>
                          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded bg-red-500/10 p-2 text-xs text-red-400">
                            {message.metadata.details}
                          </pre>
                        </details>
                      )}
                    </div>
                  </div>
                </div>
              </div>
              </>
            )
          }

          // Compaction messages: show as historical summary card
          if (message.message_type === 'compaction') {
            return (
              <>
                {daySeparator}
              <div className="px-4 py-3 transition-all">
                <div className="rounded-lg border border-slate-700/50 bg-slate-800/30 px-3 py-2.5">
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <span className="font-medium text-slate-400">📋 历史摘要</span>
                    <span className="text-slate-600">{formatTime(message.created_at)}</span>
                  </div>
                  <div className="text-sm text-slate-400 leading-relaxed">
                    {message.parts?.map((part, i) => (
                      <span key={i}>{part.type === 'text' ? part.text : ''}</span>
                    ))}
                  </div>
                </div>
              </div>
              </>
            )
          }

          // Goal messages: system state snapshot card (change type +
          // progress, collapsible criteria / evidence audit).
          if (message.message_type === 'goal') {
            return (
              <>
                {daySeparator}
                <GoalMessage message={message} />
              </>
            )
          }

          const isStreaming = message.id === streamingMessageId
          // Queued: assistant placeholder with no parts and not currently streaming.
          const isQueued =
            message.role === 'assistant' &&
            message.metadata?.queue_status === 'queued' &&
            !isStreaming
          if (message.role === 'user') {
            return (
              <>
                {daySeparator}
                <MessageBubble
                  message={message}
                  layout={chatLayout}
                  hideCodeTail={message.session_id.startsWith('dag:')}
                />
              </>
            )
          }
          return (
            <>
              {daySeparator}
              <AssistantMessage
                message={message}
                isStreaming={isStreaming}
                isQueued={isQueued}
                layout={chatLayout}
              />
            </>
          )
        }}
        followOutput="smooth"
        overscan={200}
        style={{ height: '100%' }}
      />
    </div>
  )
}