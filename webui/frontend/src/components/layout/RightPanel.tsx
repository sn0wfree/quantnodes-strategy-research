import * as Tabs from '@radix-ui/react-tabs'
import { useLayoutStore } from '../../stores/layout'
import { useWorkflowStore } from '../../stores/workflow'
import { Workflow, Target, Bot } from 'lucide-react'
import { AgentList } from '../agent/AgentList'
import { GoalTab } from '../goal/GoalTab'
import { WorkflowDAG } from '../workflow/WorkflowDAG'

const TABS = [
  { value: 'dag', label: 'DAG', icon: Workflow },
  { value: 'goal', label: 'Goal', icon: Target },
  { value: 'agent', label: 'Agent', icon: Bot },
] as const

export function RightPanel() {
  const visible = useLayoutStore((s) => s.rightPanelVisible)
  const tab = useLayoutStore((s) => s.rightPanelTab)
  const setTab = useLayoutStore((s) => s.setRightPanelTab)

  // Workflow state
  const dagNodes = useWorkflowStore((s) => s.dagNodes)
  const dagEdges = useWorkflowStore((s) => s.dagEdges)
  const executionProgress = useWorkflowStore((s) => s.executionProgress)
  const presets = useWorkflowStore((s) => s.presets)
  const currentPresetId = useWorkflowStore((s) => s.currentPresetId)
  const currentPreset = presets.find((p) => p.id === currentPresetId)

  if (!visible) return null

  return (
    <div className="flex h-full w-[480px] flex-col border-l border-slate-800 bg-slate-900/80">
      <Tabs.Root value={tab} onValueChange={(v) => setTab(v as any)}>
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

        <Tabs.Content value="dag" className="flex-1 overflow-hidden">
          <WorkflowDAG
            workflowName={currentPreset?.name || '未命名工作流'}
            nodes={dagNodes.map((n) => ({
              ...n,
              agentColor: n.status === 'running' ? '#3b82f6' : undefined,
            }))}
            edges={dagEdges}
            progress={executionProgress}
            completed={dagNodes.filter((n) => n.status === 'completed').length}
            total={dagNodes.length}
          />
        </Tabs.Content>
        <Tabs.Content value="goal" className="flex-1 overflow-y-auto p-4">
          <GoalTab goal={null} />
        </Tabs.Content>
        <Tabs.Content value="agent" className="flex-1 overflow-y-auto p-4">
          <AgentList />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  )
}
