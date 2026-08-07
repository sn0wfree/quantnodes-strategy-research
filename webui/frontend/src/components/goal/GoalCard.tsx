import { Target } from 'lucide-react'
import { GoalTab, type GoalTabGoal } from './GoalTab'
import { EquityCurveCard } from '../performance/EquityCurveCard'
import type { EquityCurve } from '../../utils/equityCurve'

interface GoalCardProps {
  goal: GoalTabGoal | null
  curve: EquityCurve | null
}

/**
 * Right-panel card merging the research goal (GoalTab) with the
 * backtest performance curve (EquityCurveCard) into one module.
 * Part of the merged single right panel.
 */
export function GoalCard({ goal, curve }: GoalCardProps) {
  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <Target className="h-3 w-3" />
        <span>目标 &amp; 进度</span>
      </div>
      <div className="space-y-4">
        <GoalTab goal={goal} />
        <div className="border-t border-slate-800" />
        <EquityCurveCard curve={curve} />
      </div>
    </div>
  )
}
