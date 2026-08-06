import * as Tabs from '@radix-ui/react-tabs'
import { useLayoutStore, type RightPanelTab } from '../../stores/layout'
import { useWorkflowStore } from '../../stores/workflow'
import { useGoalStore } from '../../stores/goal'
import { useGoalPolling } from '../../hooks/useGoalPolling'
import { useSessionStore } from '../../stores/session'
import { useSystemStore } from '../../stores/system'
import { Target, BookOpen } from 'lucide-react'
import { GoalTab } from '../goal/GoalTab'
import { StudyTab } from '../study/StudyTab'

const TABS = [
  { value: 'goal', label: 'Goal', icon: Target },
  { value: 'study', label: 'Study', icon: BookOpen },
] as const

export function RightPanel() {
  const tab = useLayoutStore((s) => s.rightPanelTab)
  const setTab = useLayoutStore((s) => s.setRightPanelTab)

  // Goal state
  const currentGoal = useGoalStore((s) => s.currentGoal)

  // Study / Session
  const sessionId = useSessionStore((s) => s.currentSessionId ?? undefined)

  // Poll goal status while the Goal tab is open (no backend goal_* SSE)
  useGoalPolling(tab === 'goal')

  // Resolve workspace for Study creation form. Default to the
  // system workspace path, falling back to the current preset's workspace_path.
  const presets = useWorkflowStore((s) => s.presets)
  const currentPresetId = useWorkflowStore((s) => s.currentPresetId)
  const currentPreset = presets.find((p) => p.id === currentPresetId)
  const systemWorkspacePath = useSystemStore((s) => s.workspacePath)
  const workspacePath =
    systemWorkspacePath
    || (currentPreset as unknown as { workspace_path?: string })?.workspace_path
    || ''

  // Map GoalStore goal to GoalTab's expected format
  const goalTabGoal = currentGoal ? {
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
    <Tabs.Root value={tab} onValueChange={(v) => setTab(v as RightPanelTab)} className="flex h-full flex-col bg-slate-900/80">
      <Tabs.List className="flex border-b border-slate-800">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <Tabs.Trigger
              key={t.value}
              value={t.value}
              className="flex flex-1 items-center justify-center gap-2 border-b-2 border-transparent px-4 py-2.5 text-sm text-slate-400 transition-colors
                data-[state=active]:border-primary-500 data-[state=active]:text-slate-100"
            >
              <Icon className="h-4 w-4" />
              {t.label}
            </Tabs.Trigger>
          )
        })}
      </Tabs.List>

      <Tabs.Content value="goal" className="flex-1 overflow-y-auto p-4">
        <GoalTab goal={goalTabGoal} />
      </Tabs.Content>
      <Tabs.Content value="study" className="flex-1 overflow-y-auto p-4">
        <StudyTab
          sessionId={sessionId}
          workspacePath={workspacePath}
        />
      </Tabs.Content>
    </Tabs.Root>
  )
}