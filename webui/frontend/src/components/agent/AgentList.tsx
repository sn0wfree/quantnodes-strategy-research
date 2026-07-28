import { useAgentStore } from '../../stores/agents'
import { AgentItem } from './AgentItem'
import { EmptyState } from '../common/EmptyState'
import { Bot } from 'lucide-react'

interface AgentListProps {
  onSelectAgent?: (agentId: string) => void
  selectedAgentId?: string | null
}

export function AgentList({ onSelectAgent, selectedAgentId }: AgentListProps) {
  const agents = useAgentStore((s) => s.agents)
  const agentList = Array.from(agents.values()).sort((a, b) => b.created_at - a.created_at)

  if (agentList.length === 0) {
    return (
      <EmptyState
        icon={<Bot className="h-10 w-10" />}
        title="暂无 Agent"
        description="Agent 会在聊天或任务执行时自动创建"
      />
    )
  }

  return (
    <div className="space-y-2">
      {agentList.map((agent) => (
        <AgentItem
          key={agent.id}
          agent={agent}
          isSelected={agent.id === selectedAgentId}
          onSelect={() => onSelectAgent?.(agent.id)}
        />
      ))}
    </div>
  )
}
