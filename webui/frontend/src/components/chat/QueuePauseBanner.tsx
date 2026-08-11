import { Pause, Play } from 'lucide-react'
import { useChatStore } from '../../stores/chat'
import { useChatSessionId } from '../../contexts/ChatSessionContext'

/**
 * Banner shown above the message list when the per-session queue is paused
 * (i.e. the user just cancelled the in-flight attempt). Provides a button
 * to resume processing the next queued message.
 */
export function QueuePauseBanner() {
  const resumeQueue = useChatStore((s) => s.resumeQueue)
  const queueLengths = useChatStore((s) => s.queueLengths)
  const currentSessionId = useChatSessionId()
  const handleResume = () => {
    void resumeQueue()
  }
  // Pending count is for THIS session only — max-across-sessions
  // showed the wrong session's queue length (B12).
  const pending = currentSessionId
    ? queueLengths.get(currentSessionId) ?? 0
    : 0
  return (
    <div
      className="flex items-center justify-between gap-3 border-b border-amber-700/40 bg-amber-900/20 px-4 py-2 text-xs text-amber-200"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <Pause className="h-3.5 w-3.5" />
        <span>
          队列已暂停{pending > 0 ? `（剩余 ${pending} 条）` : ''}，等待确认后再处理下一条
        </span>
      </div>
      <button
        type="button"
        onClick={handleResume}
        className="inline-flex items-center gap-1.5 rounded-md border border-amber-600/60 bg-amber-700/30 px-2.5 py-1 text-amber-100 hover:bg-amber-700/50"
        data-testid="queue-resume-btn"
      >
        <Play className="h-3 w-3" />
        继续下一条
      </button>
    </div>
  )
}