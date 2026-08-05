// WorkflowPage unit tests — presets list, graph load, start, history restore.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      workflow: {
        list: vi.fn(),
        graph: vi.fn(),
        start: vi.fn(),
        status: vi.fn(),
        pause: vi.fn(),
        resume: vi.fn(),
      },
      goal: {
        list: vi.fn(),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    ArrowLeft: Stub, Play: Stub, History: Stub, RefreshCw: Stub,
    Workflow: Stub, Clock: Stub, CheckCircle: Stub, XCircle: Stub,
    AlertCircle: Stub, Loader2: Stub, Bot: Stub, FileText: Stub,
    Inbox: Stub, Pause: Stub, RotateCcw: Stub, Wrench: Stub, X: Stub,
    Zap: Stub, BarChart3: Stub,
  }
})

vi.mock('@xyflow/react', () => {
  return {
    ReactFlow: () => null,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    useNodesState: () => [[], () => {}, () => {}],
    useEdgesState: () => [[], () => {}, () => {}],
    BackgroundVariant: { Dots: 'dots' },
  }
})

import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { WorkflowPage } from '../components/workflow/WorkflowPage'

const mockList = vi.mocked(api.workflow.list)
const mockGraph = vi.mocked(api.workflow.graph)
const mockStart = vi.mocked(api.workflow.start)
const mockWfStatus = vi.mocked(api.workflow.status)
const mockGoalList = vi.mocked(api.goal.list)

describe('WorkflowPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useSessionStore.setState({ currentSessionId: 'sess-1' })
    mockList.mockResolvedValue({
      status: 'ok',
      workflows: [
        { name: 'factor_research', description: '因子研究' },
        { name: 'risk_assessment', description: '风险评估' },
      ],
    } as never)
    mockGraph.mockResolvedValue({
      status: 'ok',
      name: 'factor_research',
      nodes: [{ id: 'researcher', label: 'researcher' }],
      edges: [],
    } as never)
    mockGoalList.mockResolvedValue({
      status: 'ok',
      goals: [
        {
          goal_id: 'g-hist',
          session_id: 'sess-1',
          goal_status: 'complete',
          objective: '历史目标',
          workflow_id: 'factor_research',
          created_at: '2026-08-01T10:00:00',
        },
      ],
    } as never)
  })

  it('lists presets and history goals on mount', async () => {
    render(
      <MemoryRouter>
        <WorkflowPage />
      </MemoryRouter>
    )
    expect((await screen.findAllByText('factor_research')).length).toBeGreaterThan(0)
    expect(screen.getByText('risk_assessment')).toBeInTheDocument()
    expect(screen.getAllByText(/历史目标/).length).toBeGreaterThan(1)
    expect(screen.getAllByText(/已完成/).length).toBeGreaterThan(0)
  })

  it('selecting a preset loads its graph', async () => {
    render(
      <MemoryRouter>
        <WorkflowPage />
      </MemoryRouter>
    )
    fireEvent.click((await screen.findAllByText('factor_research'))[0])
    await waitFor(() => expect(mockGraph).toHaveBeenCalledWith('factor_research'))
    expect(screen.queryByText('选择工作流模板')).not.toBeInTheDocument()
  })

  it('starts a workflow with the entered objective', async () => {
    mockStart.mockResolvedValue({
      status: 'ok',
      goal_id: 'g-new',
      workflow_name: 'factor_research',
    } as never)
    render(
      <MemoryRouter>
        <WorkflowPage />
      </MemoryRouter>
    )
    fireEvent.click((await screen.findAllByText('factor_research'))[0])
    const textarea = await screen.findByPlaceholderText(/例：找出/)
    fireEvent.change(textarea, { target: { value: 'Sharpe > 1.5' } })
    fireEvent.click(screen.getByRole('button', { name: /启动工作流/ }))
    await waitFor(() =>
      expect(mockStart).toHaveBeenCalledWith(
        'sess-1', 'factor_research', 'Sharpe > 1.5'
      )
    )
  })

  it('restores a historical goal: loads graph and checks workflow status', async () => {
    mockWfStatus.mockResolvedValue({
      status: 'not_found',
      goal_id: 'g-hist',
      workflow_name: 'factor_research',
    } as never)
    render(
      <MemoryRouter>
        <WorkflowPage />
      </MemoryRouter>
    )
    const goalBtn = await waitFor(() => {
      const found = screen
        .getAllByRole('button')
        .find((b) => b.textContent?.includes('历史目标'))
      expect(found).toBeTruthy()
      return found
    })
    fireEvent.click(goalBtn!)
    await waitFor(() => expect(mockGraph).toHaveBeenCalledWith('factor_research'))
    await waitFor(() => expect(mockWfStatus).toHaveBeenCalledWith('g-hist'))
    expect(screen.getAllByText(/已完成/).length).toBeGreaterThan(0)
  })

  it('shows start button disabled when no session is open', async () => {
    useSessionStore.setState({ currentSessionId: null })
    render(
      <MemoryRouter>
        <WorkflowPage />
      </MemoryRouter>
    )
    fireEvent.click((await screen.findAllByText('factor_research'))[0])
    const btn = screen.getByRole('button', { name: /启动工作流/ })
    expect(btn).toBeDisabled()
    expect(screen.getByText('需要先打开一个会话')).toBeInTheDocument()
  })
})
