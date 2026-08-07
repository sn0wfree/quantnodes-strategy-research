import { useState } from 'react'
import { useAgentStore } from '../../stores/agents'
import { AgentList } from '../agent/AgentList'

/**
 * Right-panel "Agent" tab — displays all agents in the current session
 * with their status, tool call counts, and iteration details.
 */
export function AgentActivityTab() {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)

  return (
    <div className="flex h-full flex-col gap-4">
      <AgentList
        selectedAgentId={selectedAgentId}
        onSelectAgent={setSelectedAgentId}
      />
    </div>
  )
}
