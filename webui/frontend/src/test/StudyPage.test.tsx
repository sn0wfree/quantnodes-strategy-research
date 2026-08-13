// StudyPage three-column dashboard tests — task list rendering, filter
// chips, selection → summary panel, and the create flow.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        list: vi.fn(),
        summary: vi.fn(),
        start: vi.fn(),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    BookOpen: Stub, Clock: Stub, ArrowRight: Stub, RefreshCw: Stub,
    Target: Stub, Activity: Stub, Layers: Stub, Eye: Stub, EyeOff: Stub,
    Plus: Stub, Pause: Stub, Play: Stub, X: Stub, Send: Stub,
    ChevronRight: Stub, ChevronDown: Stub, AlertTriangle: Stub,
    ExternalLink: Stub, BarChart3: Stub, Bot: Stub, FileText: Stub,
    Inbox: Stub, RotateCcw: Stub, Wrench: Stub, Zap: Stub,
    Search: Stub, MessageSquare: Stub, Settings: Stub, Workflow: Stub,
    Circle: Stub, CheckCircle2: Stub, Sparkles: Stub, Sun: Stub, Moon: Stub,
    Columns2: Stub, PanelRight: Stub, PanelRightClose: Stub, Pencil: Stub,
    FileCode: Stub, LineChart: Stub, TrendingUp: Stub, TrendingDown: Stub,
    ListChecks: Stub, Library: Stub, LogOut: Stub, Network: Stub, Sigma: Stub,
    SlidersHorizontal: Stub, FolderOpen: Stub, User: Stub,
  }
})

import { api } from '../api/client'
import { StudyPage } from '../pages/StudyPage'
import { useSessionStore } from '../stores/session'
import { useStudyStore } from '../stores/study'
import { useSystemStore } from '../stores/system'

const mockList = vi.mocked(api.study.list)
const mockSummary = vi.mocked(api.study.summary)
const mockStart = vi.mocked(api.study.start)

const STUDIES = [
  {
    study_id: 'st-1',
    session_id: 's-1',
    objective: '动量因子研究',
    strategy_name: 'momentum',
    workspace_path: '/ws',
    execution_status: 'running',
    current_round: 2,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-01T12:00:00Z',
  },
  {
    study_id: 'st-2',
    session_id: 's-1',
    objective: '价值因子研究',
    strategy_name: 'value',
    workspace_path: '/ws',
    execution_status: 'complete',
    current_round: 5,
    created_at: '2026-07-20T10:00:00Z',
    updated_at: '2026-07-22T10:00:00Z',
  },
]

function summaryFixture(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 2,
    max_rounds: 5,
    objective: '动量因子研究',
    strategy_name: 'momentum',
    workspace_path: '/ws',
    created_at: '2026-08-01T10:00:00',
    updated_at: '2026-08-01T12:00:00',
    recent_rounds: [
      {
        round_num: 2,
        run_name: 'run_0002',
        metrics: { sharpe: 1.2, calmar: 0.8, max_dd: -0.1 },
        verdict: 'keep',
        created_at: '2026-08-01T10:30:00',
      },
      {
        round_num: 1,
        run_name: 'run_0001',
        metrics: { sharpe: 0.5, calmar: 0.2, max_dd: -0.3 },
        verdict: 'discard',
        created_at: '2026-08-01T10:10:00',
      },
    ],
    scoreboard: [],
    goal_snapshot: {
      goal_id: 'g-1',
      goal_status: 'active',
      objective: '动量因子研究',
      progress_percent: 40,
      evidence_count: 2,
      criteria: [],
    },
    ...overrides,
  }
}

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 's-1' } as never)
  useStudyStore.setState({ current: null, list: [], busy: false, error: '' })
  useSystemStore.setState({ workspacePath: '/ws' } as never)
  mockList.mockResolvedValue({ status: 'ok', studies: STUDIES } as never)
  mockSummary.mockResolvedValue(summaryFixture() as never)
  vi.clearAllMocks()
})

describe('StudyPage', () => {
  it('renders header, create panel, task list and auto-selects summary', async () => {
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Study 研究任务')).toBeTruthy()
    expect(screen.getByText('任务列表')).toBeTruthy()
    expect(screen.getByPlaceholderText(/研究 A 股动量因子/)).toBeTruthy()
    await waitFor(() => {
      expect(screen.getByText('动量因子研究')).toBeTruthy()
      expect(screen.getByText('价值因子研究')).toBeTruthy()
    })
    // Auto-selected the running study → summary fetched and rendered
    await waitFor(() => {
      expect(mockSummary).toHaveBeenCalledWith('st-1')
    })
    expect(await screen.findByText('任务摘要')).toBeTruthy()
  })

  it('switches the summary when clicking another task', async () => {
    mockSummary.mockResolvedValue(
      summaryFixture({ study_id: 'st-2', objective: '价值因子研究', execution_status: 'complete' }) as never
    )
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('价值因子研究')).toBeTruthy())
    fireEvent.click(screen.getAllByText('价值因子研究')[0])
    await waitFor(() => expect(mockSummary).toHaveBeenCalledWith('st-2'))
    expect(await screen.findByText('任务摘要')).toBeTruthy()
  })

  it('filters the task list by status chips', async () => {
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('价值因子研究')).toBeTruthy())
    fireEvent.click(screen.getByText('进行中'))
    expect(screen.getByText('动量因子研究')).toBeTruthy()
    expect(screen.queryByText('价值因子研究')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('已完成'))
    expect(screen.queryByText('动量因子研究')).not.toBeInTheDocument()
    expect(screen.getByText('价值因子研究')).toBeTruthy()
    fireEvent.click(screen.getByText('全部'))
    expect(screen.getByText('动量因子研究')).toBeTruthy()
    expect(screen.getByText('价值因子研究')).toBeTruthy()
  })

  it('starts a study from the create panel and selects it', async () => {
    mockStart.mockResolvedValue({
      status: 'ok', study_id: 'st-new', execution_status: 'queued',
    } as never)
    mockList.mockResolvedValue({
      status: 'ok',
      studies: [...STUDIES, { ...STUDIES[0], study_id: 'st-new', objective: '反转因子研究' }],
    } as never)
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    const input = await screen.findByPlaceholderText(/研究 A 股动量因子/)
    fireEvent.change(input, { target: { value: '反转因子研究' } })
    const nameInput = await screen.findByPlaceholderText(/自动生成/)
    fireEvent.change(nameInput, { target: { value: 'reversal_7d' } })
    fireEvent.click(screen.getByRole('button', { name: /启动 study/ }))
    await waitFor(() => expect(mockStart).toHaveBeenCalled())
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
  })

  it('shows a session prompt in the create panel without a session', () => {
    useSessionStore.setState({ currentSessionId: null } as never)
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    expect(screen.getByText('尚未选择 session')).toBeTruthy()
  })
})
