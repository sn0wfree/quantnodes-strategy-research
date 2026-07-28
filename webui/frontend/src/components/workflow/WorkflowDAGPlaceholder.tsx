import { Workflow } from 'lucide-react'
import { EmptyState } from '../common/EmptyState'

export function WorkflowDAGPlaceholder() {
  return (
    <EmptyState
      icon={<Workflow className="h-10 w-10" />}
      title="DAG 视图"
      description="启动工作流后显示 DAG 可视化"
    />
  )
}
