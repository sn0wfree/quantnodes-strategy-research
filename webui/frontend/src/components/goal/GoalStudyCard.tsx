import { Target } from 'lucide-react'
import { GoalTab, type GoalTabGoal } from './GoalTab'
import { StudyTab } from '../study/StudyTab'

interface GoalStudyCardProps {
  goal: GoalTabGoal | null
  sessionId: string | undefined
  workspacePath: string
}

/**
 * Right-panel card merging the research goal (GoalTab) and the study /
 * autoresearch task system (StudyTab) into one module with two stacked
 * sections. Part of the merged single right panel.
 */
export function GoalStudyCard({ goal, sessionId, workspacePath }: GoalStudyCardProps) {
  return (
    <div className="rounded-lg border border-slate-800/50 bg-slate-900/30 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-slate-500">
        <Target className="h-3 w-3" />
        <span>目标 &amp; Study</span>
      </div>
      <div className="space-y-4">
        <GoalTab goal={goal} />
        <div className="border-t border-slate-800" />
        <StudyTab sessionId={sessionId} workspacePath={workspacePath} />
      </div>
    </div>
  )
}
