import { Workflow } from 'lucide-react'
import { EmptyState } from '../common/EmptyState'

// TODO(feature): no callers today. The right panel renders its own
// empty state ("未命名工作流"); this placeholder was written for a
// dedicated DAG view that was never wired. Keep for the workflow
// workspace feature, or remove together with that feature's plan.

export function WorkflowDAGPlaceholder() {
  return (
    <EmptyState
      icon={<Workflow className="h-10 w-10" />}
      title="DAG 视图"
      description="启动工作流后显示 DAG 可视化"
    />
  )
}
