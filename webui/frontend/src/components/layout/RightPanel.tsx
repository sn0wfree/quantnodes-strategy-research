import { useMemo } from 'react'
import { useGoalStore } from '../../stores/goal'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import {
  extractEquityCurve,
  extractLatestBacktestMetrics,
} from '../../utils/equityCurve'
import { TokenCard } from '../context/TokenCard'
import { GoalCard } from '../goal/GoalCard'
import type { GoalTabGoal } from '../goal/GoalTab'

/**
 * Merged single right panel: a scrollable column of cards —
 * token usage, and goal + performance curve.
 */
export function RightPanel() {
  // Goal state is SSE-driven (full-snapshot goal_updated events) plus
  // loadSessionState recovery on session switch / page load — no
  // polling (docs/goal-events-panel-link.md).
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

  // Metrics-only fallback for the right-panel card (Tier B P7).
  // When no chart parts are available (the backend does not emit
  // chart SSE), the most recent run_backtest tool_call result still
  // gives us total_return / sharpe / max_drawdown so the panel
  // never reads "no data" after a real backtest.
  const metrics = useMemo(() => {
    const list = Array.from(messages.values())
      .filter((m) => !currentSessionId || m.session_id === currentSessionId)
      .sort((a, b) => a.created_at - b.created_at)
    return extractLatestBacktestMetrics(list)
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
      <GoalCard goal={goalTabGoal} curve={curve} metrics={metrics} />
    </div>
  )
}
