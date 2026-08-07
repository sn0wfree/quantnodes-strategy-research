import { useMemo } from 'react'
import { useLayoutStore } from '../../stores/layout'
import { useGoalStore } from '../../stores/goal'
import { useGoalPolling } from '../../hooks/useGoalPolling'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { extractEquityCurve } from '../../utils/equityCurve'
import { TokenCard } from '../context/TokenCard'
import { GoalCard } from '../goal/GoalCard'
import type { GoalTabGoal } from '../goal/GoalTab'

/**
 * Merged single right panel: a scrollable column of cards —
 * token usage, and goal + performance curve.
 */
export function RightPanel() {
  const rightPanelVisible = useLayoutStore((s) => s.rightPanelVisible)

  // Poll goal status while the panel is open (no backend goal_* SSE)
  useGoalPolling(rightPanelVisible)

  // Goal state
  const currentGoal = useGoalStore((s) => s.currentGoal)

  // Session / messages
  const currentSessionId = useSessionStore((s) => s.currentSessionId)
  const messages = useChatStore((s) => s.messages)

  // Performance curve decoded from the session's backtest output
  const curve = useMemo(() => {
    const list = Array.from(messages.values())
      .filter((m) => !currentSessionId || m.session_id === currentSessionId)
      .sort((a, b) => a.created_at - b.created_at)
    return extractEquityCurve(list)
  }, [messages, currentSessionId])

  // Map GoalStore goal to the display model used by GoalTab
  const goalTabGoal: GoalTabGoal | null = currentGoal ? {
    id: currentGoal.goal_id,
    title: currentGoal.objective,
    description: '',
    status: currentGoal.status === 'complete' ? 'completed' as const
      : currentGoal.status === 'cancelled' ? 'failed' as const
      : 'active' as const,
    criteria: currentGoal.criteria.map((c) => ({
      id: c.criterion_id,
      description: c.text,
      status: c.status === 'covered' ? 'completed' as const
        : c.status === 'pending' ? 'pending' as const
        : 'in_progress' as const,
      evidence_count: c.evidence_count ?? 0,
    })),
    timeline: [],
  } : null

  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-y-auto bg-slate-900 p-3">
      <TokenCard />
      <GoalCard goal={goalTabGoal} curve={curve} />
    </div>
  )
}
