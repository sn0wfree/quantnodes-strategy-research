// StudyDAGComposer — AI plan flow and the fixed manual-edit behavior:
// palette edits KEEP the planner edges between still-selected agents.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: {
    study: { agents: vi.fn(), planDag: vi.fn() },
  },
}))

vi.mock('../components/study/StudyAgentPalette', () => ({
  StudyAgentPalette: ({
    selected, onChange,
  }: {
    selected: Set<string>
    onChange: (next: Set<string>) => void
  }) => (
    <div data-testid="palette">
      <button onClick={() => onChange(new Set(['researcher']))}>
        PALETTE_SELECT_RESEARCHER
      </button>
      <button
        onClick={() => onChange(new Set(['researcher', 'strategist']))}
      >
        PALETTE_ADD_STRATEGIST
      </button>
      <span>SEL:{[...selected].sort().join(',')}</span>
    </div>
  ),
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Loader2',
    'Sparkles',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { StudyDAGComposer } from '../components/study/StudyDAGComposer'

const mockAgents = vi.mocked(api.study.agents)
const mockPlanDag = vi.mocked(api.study.planDag)

const PLAN = {
  status: 'ok' as const,
  selected_agents: ['researcher', 'strategist', 'risk_controller'],
  reasoning: '动量研究需要假设→编排→风控链路',
  graph: {
    nodes: [
      { id: 'researcher', type: 'llm_agent', label: '研究员', enabled: true },
      { id: 'strategist', type: 'planner', label: '策略师', enabled: true },
      { id: 'risk_controller', type: 'llm_agent', label: '风控', enabled: true },
    ],
    edges: [],
  },
  dag_config: { dag: { strategist: ['researcher'], risk_controller: ['strategist'] } },
}

describe('StudyDAGComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAgents.mockResolvedValue({
      status: 'ok',
      required: ['researcher'],
      agents: [
        { id: 'researcher', name: '研究员', category: 'research', description: '', keywords: [] },
        { id: 'strategist', name: '策略师', category: 'planning', description: '', keywords: [] },
        { id: 'risk_controller', name: '风控', category: 'risk', description: '', keywords: [] },
      ],
    } as never)
    mockPlanDag.mockResolvedValue(PLAN as never)
  })

  it('AI 推荐 posts planDag and hands the full graph to onGraphReady', async () => {
    const onGraphReady = vi.fn()
    render(<StudyDAGComposer objective="动量因子研究" onGraphReady={onGraphReady} />)
    fireEvent.click(await screen.findByRole('button', { name: /AI 推荐/ }))
    await waitFor(() => {
      expect(mockPlanDag).toHaveBeenCalledWith({
        objective: '动量因子研究',
        max_agents: 12,
      })
    })
    expect(onGraphReady).toHaveBeenCalledTimes(1)
    const [graph, agents] = onGraphReady.mock.calls[0]
    // planner edges derived from dag_config: researcher→strategist→risk
    expect(graph.edges).toEqual([
      { source: 'researcher', target: 'strategist' },
      { source: 'strategist', target: 'risk_controller' },
    ])
    expect(agents).toEqual([
      'researcher', 'strategist', 'risk_controller',
    ])
    expect(screen.getByText(/动量研究需要假设/)).toBeInTheDocument()
  })

  it('blocks planning when the objective is empty', async () => {
    const onGraphReady = vi.fn()
    render(<StudyDAGComposer objective="   " onGraphReady={onGraphReady} />)
    const btn = await screen.findByRole('button', { name: /AI 推荐/ })
    expect(btn).toBeDisabled()
    expect(mockPlanDag).not.toHaveBeenCalled()
  })

  it('manual palette edits KEEP planned edges between selected agents', async () => {
    const onGraphReady = vi.fn()
    render(<StudyDAGComposer objective="x" onGraphReady={onGraphReady} />)
    await screen.findByRole('button', { name: /AI 推荐/ })
    fireEvent.click(screen.getByRole('button', { name: /AI 推荐/ }))
    await waitFor(() => expect(onGraphReady).toHaveBeenCalledTimes(1))

    // drop risk_controller → its edge (strategist→risk) must be dropped,
    // but researcher→strategist must SURVIVE (old code wiped all edges)
    fireEvent.click(screen.getByRole('button', { name: 'PALETTE_SELECT_RESEARCHER' }))
    fireEvent.click(screen.getByRole('button', { name: 'PALETTE_ADD_STRATEGIST' }))
    await waitFor(() => expect(onGraphReady).toHaveBeenCalledTimes(3))
    const [graph] = onGraphReady.mock.calls[2]
    expect(graph.edges).toEqual([{ source: 'researcher', target: 'strategist' }])
    expect(graph.nodes.map((n: { id: string }) => n.id).sort()).toEqual([
      'researcher', 'strategist',
    ])
  })

  it('shows an error when planDag fails', async () => {
    mockPlanDag.mockRejectedValueOnce(new Error('LLM 不可用') as never)
    render(<StudyDAGComposer objective="x" onGraphReady={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /AI 推荐/ }))
    expect(await screen.findByText(/LLM 不可用/)).toBeInTheDocument()
  })
})
