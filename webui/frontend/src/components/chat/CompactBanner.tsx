import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { useChatStore } from '../../stores/chat'

/**
 * Banner shown above the message list when context compaction occurs.
 * Auto-dismisses after 5 seconds.
 */
export function CompactBanner() {
  const lastCompaction = useChatStore((s) => s.lastCompaction)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (lastCompaction) {
      setVisible(true)
      const timer = setTimeout(() => setVisible(false), 5000)
      return () => clearTimeout(timer)
    }
  }, [lastCompaction])

  if (!visible || !lastCompaction) return null

  return (
    <div
      className="flex items-center gap-2 border-b border-emerald-700/40 bg-emerald-900/20 px-4 py-2 text-xs text-emerald-200"
      role="status"
      aria-live="polite"
    >
      <Check className="h-3.5 w-3.5" />
      <span>上下文已压缩: {lastCompaction.layer}</span>
    </div>
  )
}
