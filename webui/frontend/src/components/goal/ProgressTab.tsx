import { useMemo } from 'react'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { extractEquityCurve } from '../../utils/equityCurve'
import { EquityCurveCard } from '../performance/EquityCurveCard'
import { GoalTab, type GoalTabGoal } from './GoalTab'

export type ProgressTabGoal = GoalTabGoal

/**
 * Right-panel "Progress" tab: merges the active research goal's progress
 * with the equity/performance curve decoded from the session's backtest
 * output. Goal progress on top via GoalTab, performance curve below.
 */
export function ProgressTab({ goal }: { goal: GoalTabGoal | null }) {
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const messages = useChatStore((s) => s.messages)

  const curve = useMemo(() => {
    const list = Array.from(messages.values())
      .filter((m) => !currentSessionId || m.session_id === currentSessionId)
      .sort((a, b) => a.created_at - b.created_at)
    return extractEquityCurve(list)
  }, [messages, currentSessionId])

  return (
    <div className="space-y-6">
      <EquityCurveCard curve={curve} />
      <GoalTab goal={goal} />
    </div>
  )
}