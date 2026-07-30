import { Pause, Play } from 'lucide-react'
import { useChatStore } from '../../stores/chat'

/**
 * Banner shown above the message list when the per-session queue is paused
 * (i.e. the user just cancelled the in-flight attempt). Provides a button
 * to resume processing the next queued message.
 */
export function QueuePauseBanner() {
  const resumeQueue = useChatStore((s) => s.resumeQueue)
  const queueLengths = useChatStore((s) => s.queueLengths)
  // The banner doesn't have direct access to currentSessionId from props,
  // but resumeQueue reads it from sessionStore internally.
  const handleResume = () => {
    void resumeQueue()
  }
  // queueLengths is a Map; render the largest queued count we have across
  // sessions if multiple are tracked. Single-session UI is the common case.
  const pending = Math.max(0, ...Array.from(queueLengths.values()))
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