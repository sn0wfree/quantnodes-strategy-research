// StudyDetailPage unit tests — mocks the api client, exercises the
// summary/directives rendering, control buttons and 404 empty state.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        summary: vi.fn(),
        directives: vi.fn(),
        pause: vi.fn(),
        resume: vi.fn(),
        cancel: vi.fn(),
        directive: vi.fn(),
      },
    },
    ApiError: class extends Error {
      status: number
      constructor(status: number, message: string) {
        super(message)
        this.status = status
      }
    },
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    ArrowLeft: Stub, Pause: Stub, Play: Stub, X: Stub, Send: Stub,
    Clock: Stub, FolderOpen: Stub, User: Stub, Target: Stub,
    ChevronRight: Stub, ChevronDown: Stub, AlertTriangle: Stub,
    ExternalLink: Stub, BarChart3: Stub, Bot: Stub, FileText: Stub,
    Inbox: Stub, RotateCcw: Stub, Wrench: Stub, Zap: Stub,
    Activity: Stub, Layers: Stub, Eye: Stub, EyeOff: Stub, Plus: Stub,
    ArrowRight: Stub, Search: Stub, MessageSquare: Stub,
    Settings: Stub, Workflow: Stub, BookOpen: Stub,
  }
})

import { api } from '../api/client'
import { StudyDetailPage } from '../components/study/StudyDetailPage'

const mockSummary = vi.mocked(api.study.summary)
const mockDirectives = vi.mocked(api.study.directives)

function summaryFixture(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 2,
    max_rounds: 5,
    objective: '找到 alpha 因子',
    strategy_name: 'mom_20d',
    workspace_path: '/tmp/ws',
    created_at: '2026-08-01T10:00:00',
    updated_at: '2026-08-01T11:00:00',
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
        verdict: 'reject',
        created_at: '2026-08-01T10:10:00',
      },
    ],
    scoreboard: [
      { lever: 'momentum', precision_mean: 0.5, attempts: 2, accepted: 1, reverted: 0 },
    ],
    goal_snapshot: {
      goal_id: 'g-1',
      goal_status: 'active',
      objective: '找到 alpha 因子',
      progress_percent: 40,
      evidence_count: 2,
      criteria: [
        { criterion_id: 'c-1', text: 'Sharpe > 1', status: 'pending', required: true },
      ],
    },
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/study/st-1']}>
      <Routes>
        <Route path="/study/:studyId" element={<StudyDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('StudyDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSummary.mockResolvedValue(summaryFixture() as never)
    mockDirectives.mockResolvedValue({
      status: 'ok',
      study_id: 'st-1',
      directives: [
        {
          directive_id: 'd-1',
          content: '改成动量因子',
          issued_by: 'webui',
          created_at: '2026-08-01T09:00:00',
          consumed_at: null,
        },
      ],
    } as never)
  })

  it('renders summary data: objective, status, rounds, scoreboard', async () => {
    renderPage()
    expect(await screen.findByText(/mom_20d/)).toBeInTheDocument()
    expect(screen.getAllByText('找到 alpha 因子').length).toBeGreaterThan(0)
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText(/Round 2\/5/)).toBeInTheDocument()
    expect(screen.getByText('run_0002')).toBeInTheDocument()
    expect(screen.getByText('momentum')).toBeInTheDocument()
    expect(mockSummary).toHaveBeenCalledWith('st-1')
  })

  it('shows the pending directive with its content', async () => {
    renderPage()
    expect(await screen.findByText(/改成动量因子/)).toBeInTheDocument()
    expect(screen.getByText('待消费')).toBeInTheDocument()
    expect(mockDirectives).toHaveBeenCalledWith('st-1')
  })

  it('renders pause + cancel for a running study, not resume', async () => {
    renderPage()
    await screen.findByText(/mom_20d/)
    expect(screen.getByRole('button', { name: /暂停/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /取消/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /恢复/ })).not.toBeInTheDocument()
  })

  it('calls the pause API and surfaces errors', async () => {
    vi.mocked(api.study.pause).mockResolvedValue({
      status: 'ok', study_id: 'st-1', action: 'pause',
    } as never)
    vi.mocked(api.study.pause).mockRejectedValueOnce(new Error('pause failed') as never)
    renderPage()
    await screen.findByText(/mom_20d/)
    fireEvent.click(screen.getByRole('button', { name: /暂停/ }))
    expect(await screen.findByText(/pause failed/)).toBeInTheDocument()
  })

  it('shows resume button for a paused study', async () => {
    mockSummary.mockResolvedValue(
      summaryFixture({ execution_status: 'paused' }) as never
    )
    renderPage()
    await screen.findByText(/mom_20d/)
    expect(screen.getByRole('button', { name: /恢复/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /暂停/ })).not.toBeInTheDocument()
  })

  it('shows the 404 empty state when the study is missing', async () => {
    mockSummary.mockRejectedValueOnce(
      new (class extends Error {
        status = 404
      })('not found') as never
    )
    renderPage()
    expect(await screen.findByText(/研究任务不存在/)).toBeInTheDocument()
  })
})

describe('StudyDetailPage interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSummary.mockResolvedValue(summaryFixture() as never)
    mockDirectives.mockResolvedValue({
      status: 'ok',
      study_id: 'st-1',
      directives: [],
    } as never)
  })

  it('calls resume for a paused study', async () => {
    mockSummary.mockResolvedValue(
      summaryFixture({ execution_status: 'paused' }) as never
    )
    vi.mocked(api.study.resume).mockResolvedValue({
      status: 'ok', study_id: 'st-1', action: 'resumed',
    } as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /恢复/ }))
    await waitFor(() => expect(api.study.resume).toHaveBeenCalledWith('st-1'))
  })

  it('submits a directive and refreshes the list', async () => {
    vi.mocked(api.study.directive).mockResolvedValue({
      status: 'ok', study_id: 'st-1', directive_id: 'd-new',
      created_at: '2026-08-01T12:00:00',
    } as never)
    mockDirectives
      .mockResolvedValueOnce({
        status: 'ok', study_id: 'st-1', directives: [],
      } as never)
      .mockResolvedValueOnce({
        status: 'ok', study_id: 'st-1',
        directives: [
          {
            directive_id: 'd-new', content: '试试反转因子',
            issued_by: 'webui', created_at: '2026-08-01T12:00:00',
            consumed_at: null,
          },
        ],
      } as never)

    renderPage()
    const input = await screen.findByPlaceholderText(/改成动量因子/)
    fireEvent.change(input, { target: { value: '  试试反转因子  ' } })
    fireEvent.click(screen.getByRole('button', { name: /提交指令/ }))

    await waitFor(() =>
      expect(api.study.directive).toHaveBeenCalledWith('st-1', '试试反转因子', 'webui')
    )
    // Trimmed content + refreshed list appear; input is cleared.
    expect(await screen.findByText('试试反转因子')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/改成动量因子/)).toHaveValue('')
  })

  it('surfaces directive submission errors', async () => {
    vi.mocked(api.study.directive).mockRejectedValueOnce(
      new Error('directive rejected') as never
    )
    renderPage()
    const input = await screen.findByPlaceholderText(/改成动量因子/)
    fireEvent.change(input, { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /提交指令/ }))
    expect(await screen.findByText(/directive rejected/)).toBeInTheDocument()
  })

  it('does not submit an empty directive', async () => {
    renderPage()
    await screen.findByText(/mom_20d/)
    fireEvent.click(screen.getByRole('button', { name: /提交指令/ }))
    expect(api.study.directive).not.toHaveBeenCalled()
  })

  it('navigates to the run detail page when opening a round', async () => {
    function RunStub() {
      const { strategyName, runName } = useParams<{
        strategyName: string
        runName: string
      }>()
      return <div>RUN:{strategyName}:{runName}</div>
    }
    render(
      <MemoryRouter initialEntries={['/study/st-1']}>
        <Routes>
          <Route path="/study/:studyId" element={<StudyDetailPage />} />
          <Route path="/run/:strategyName/:runName" element={<RunStub />} />
        </Routes>
      </MemoryRouter>
    )
    const linkBtn = await waitFor(() =>
      screen.getAllByTitle('查看回测产物')[0]
    )
    fireEvent.click(linkBtn)
    expect(await screen.findByText('RUN:mom_20d:run_0002')).toBeInTheDocument()
  })
})
