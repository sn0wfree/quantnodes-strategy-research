// StudyDetailPage unit tests — mocks the api client, exercises the
// summary/directives rendering, control buttons and 404 empty state.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        summary: vi.fn(),
        summaryWithEtag: vi.fn(),
        directives: vi.fn(),
        pause: vi.fn(),
        resume: vi.fn(),
        cancel: vi.fn(),
        directive: vi.fn(),
        journal: vi.fn().mockResolvedValue({ status: 'ok', study_id: 'st-1', journal: '' }),
        hangingEvents: vi.fn().mockResolvedValue({
          status: 'ok', study_id: 'st-1', window_hours: 24, by_type: {}, recent: [],
        }),
        availableActions: vi.fn().mockResolvedValue({
          status: 'ok', study_id: 'st-1', execution_status: 'running',
          actions: [
            { name: 'pause', label: '暂停', destructive: false },
            { name: 'cancel', label: '取消', destructive: true },
          ],
        }),
        dispatchAction: vi.fn().mockResolvedValue({ status: 'ok', study_id: 'st-1', action: 'ok' }),
        redoRound: vi.fn(),
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

vi.mock('lucide-react', async () => {
  const Stub = () => null
  const names = [

    'Activity',
    'AlertCircle',
    'AlertTriangle',
    'Archive',
    'ArchiveRestore',
    'ArrowLeft',
    'ArrowRight',
    'Ban',
    'BarChart3',
    'Bold',
    'BookOpen',
    'Bot',
    'Brain',
    'Calculator',
    'CalendarCheck',
    'ChartLine',
    'Check',
    'CheckCircle',
    'CheckCircle2',
    'ChevronDown',
    'ChevronLeft',
    'ChevronRight',
    'ChevronUp',
    'Circle',
    'CircleCheck',
    'CircleDashed',
    'ClipboardList',
    'Clock',
    'Code',
    'Code2',
    'Columns2',
    'Command',
    'Copy',
    'Cpu',
    'Database',
    'Download',
    'Edit3',
    'ExternalLink',
    'Eye',
    'EyeOff',
    'FileClock',
    'FileCode2',
    'FileEdit',
    'FileJson',
    'FileText',
    'Folder',
    'FolderOpen',
    'Gauge',
    'GitCompare',
    'Globe',
    'Hammer',
    'Hash',
    'HeartPulse',
    'HelpCircle',
    'History',
    'Image',
    'Inbox',
    'Info',
    'Italic',
    'KeyRound',
    'Layers',
    'LayoutGrid',
    'Library',
    'LineChart',
    'Link2',
    'List',
    'ListChecks',
    'ListOrdered',
    'Loader2',
    'LogOut',
    'MessageSquare',
    'MessageSquareText',
    'Minimize2',
    'Minus',
    'Moon',
    'MoreVertical',
    'Network',
    'Palette',
    'PanelRight',
    'PanelRightClose',
    'Pause',
    'PenTool',
    'Pencil',
    'Play',
    'Plus',
    'Quote',
    'Redo2',
    'RefreshCw',
    'RotateCcw',
    'Save',
    'Search',
    'Send',
    'Settings',
    'Shield',
    'ShieldCheck',
    'Sigma',
    'SlidersHorizontal',
    'Sparkles',
    'Square',
    'Star',
    'Sun',
    'Table',
    'Tag',
    'Target',
    'ThumbsDown',
    'ThumbsUp',
    'Trash2',
    'TrendingDown',
    'TrendingUp',
    'Undo2',
    'User',
    'UserPlus',
    'Wifi',
    'WifiOff',
    'Workflow',
    'Wrench',
    'X',
    'XCircle',
    'Zap',
  
  ]
  const out: Record<string, unknown> = { default: Stub }
  for (const n of names) out[n] = Stub
  return out
})

vi.mock('../../hooks/useSSE', () => ({ useSSE: () => {} }))
vi.mock('../components/study/dashboard/widgets/StudyChat', () => ({
  StudyChat: ({ studyId }: { studyId: string }) => (
    <div data-testid="study-chat">CHAT:{studyId}</div>
  ),
}))
vi.mock('../components/study/MetricsCompare', () => ({ MetricsCompare: () => <div /> }))
vi.mock('../components/study/MetricsTrendChart', () => ({
  MetricsTrendChart: () => <div />,
}))
vi.mock('../components/study/RoundHistory', () => ({
  RoundHistory: ({
    rounds, currentRound, studyId,
  }: {
    rounds: Array<{ round_num: number }>
    currentRound?: number
    studyId: string
  }) => (
    <div data-testid="round-history">
      RH:{rounds.length}:{currentRound}:{studyId}
    </div>
  ),
}))
vi.mock('../components/study/EditObjectiveDialog', () => ({
  EditObjectiveDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="edit-objective-dialog">EDIT_OPEN</div> : null,
}))
vi.mock('../components/study/ContinueDialog', () => ({
  ContinueDialog: ({
    open, onContinue,
  }: {
    open: boolean
    onContinue: (mode: 'resume' | 'restart', fromRound?: number) => void
  }) =>
    open ? (
      <button onClick={() => onContinue('resume')}>DIALOG_CONTINUE</button>
    ) : null,
}))
vi.mock('../components/study/AgentApprovalDialog', () => ({
  AgentApprovalDialog: () => <div />,
}))

import { api } from '../api/client'
import { StudyDetailPage } from '../components/study/StudyDetailPage'

const mockSummary = vi.mocked(api.study.summaryWithEtag)
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
    metric_targets: [
      { name: 'calmar_ratio', op: '>=', value: 1.0 },
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
    mockSummary.mockResolvedValue({ data: summaryFixture(), etag: '"v1"' } as never)
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

  it('renders summary KPIs: objective, status, rounds, best calmar', async () => {
    renderPage()
    expect(await screen.findByText(/mom_20d/)).toBeInTheDocument()
    expect(screen.getAllByText('找到 alpha 因子').length).toBeGreaterThan(0)
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText('2/5')).toBeInTheDocument()
    // best calmar across recent_rounds = 0.8
    expect(screen.getAllByText('0.80').length).toBeGreaterThan(0)
    expect(screen.getByText('CHAT:st-1')).toBeInTheDocument()
    // conditional ETag polling passes the stored etag on first load = undefined
    expect(mockSummary).toHaveBeenCalledWith('st-1', undefined)
  })

  it('renders goal progress and evidence count from goal_snapshot', async () => {
    renderPage()
    expect(await screen.findByText('40%')).toBeInTheDocument()
    expect(screen.getByText('目标进度 · 2 证据')).toBeInTheDocument()
    // acceptance-line chips from metric_targets
    expect(screen.getByText('验收线:')).toBeInTheDocument()
    expect(screen.getByText(/calmar_ratio >= 1/)).toBeInTheDocument()
  })

  it('renders pause + cancel for a running study per availableActions', async () => {
    renderPage()
    await screen.findByText(/mom_20d/)
    expect(screen.getByRole('button', { name: /暂停/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /中止/ })).toBeInTheDocument()
    // actions list has no continue → no 继续 button
    expect(screen.queryByRole('button', { name: /继续/ })).not.toBeInTheDocument()
  })

  it('calls the pause API and surfaces errors', async () => {
    vi.mocked(api.study.dispatchAction).mockRejectedValueOnce(
      new Error('pause failed') as never
    )
    renderPage()
    await screen.findByText(/mom_20d/)
    fireEvent.click(screen.getByRole('button', { name: /暂停/ }))
    expect(await screen.findByText(/pause failed/)).toBeInTheDocument()
  })

  it('shows the continue button for a paused study', async () => {
    mockSummary.mockResolvedValue(
      { data: summaryFixture({ execution_status: 'paused' }), etag: null } as never
    )
    vi.mocked(api.study.availableActions).mockResolvedValue({
      status: 'ok', study_id: 'st-1', execution_status: 'paused',
      actions: [
        { name: 'continue', label: '继续', destructive: false },
        { name: 'cancel', label: '取消', destructive: true },
      ],
    } as never)
    renderPage()
    await screen.findByText(/mom_20d/)
    expect(screen.getByRole('button', { name: /继续/ })).toBeInTheDocument()
    expect(screen.getByText('已暂停')).toBeInTheDocument()
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
    mockSummary.mockResolvedValue({ data: summaryFixture(), etag: '"v1"' } as never)
    mockDirectives.mockResolvedValue({
      status: 'ok',
      study_id: 'st-1',
      directives: [],
    } as never)
  })

  it('continue flow: button opens dialog, dialog dispatches continue/append', async () => {
    vi.mocked(api.study.availableActions).mockResolvedValue({
      status: 'ok', study_id: 'st-1', execution_status: 'paused',
      actions: [
        { name: 'continue', label: '继续', destructive: false },
      ],
    } as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /继续/ }))
    // ContinueDialog stub exposes the callback as a button
    fireEvent.click(screen.getByRole('button', { name: 'DIALOG_CONTINUE' }))
    await waitFor(() =>
      expect(api.study.dispatchAction).toHaveBeenCalledWith('st-1', 'continue', {
        mode: 'append',
      })
    )
  })

  it('archives a study after window.confirm is accepted', async () => {
    vi.mocked(api.study.availableActions).mockResolvedValue({
      status: 'ok', study_id: 'st-1', execution_status: 'complete',
      actions: [{ name: 'archive', label: '归档', destructive: false }],
    } as never)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /归档/ }))
    await waitFor(() =>
      expect(api.study.dispatchAction).toHaveBeenCalledWith('st-1', 'archive', undefined)
    )
    confirmSpy.mockRestore()
  })

  it('cancel is a no-op when window.confirm is declined', async () => {
    vi.mocked(api.study.availableActions).mockResolvedValue({
      status: 'ok', study_id: 'st-1', execution_status: 'running',
      actions: [
        { name: 'pause', label: '暂停', destructive: false },
        { name: 'cancel', label: '取消', destructive: true },
      ],
    } as never)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /中止/ }))
    expect(api.study.dispatchAction).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('renders the collapsible round history with round count', async () => {
    renderPage()
    const toggle = await screen.findByRole('button', { name: /轮次历史/ })
    expect(toggle).toHaveTextContent('2 轮')
    // collapsed by default → RoundHistory not mounted yet
    expect(screen.queryByTestId('round-history')).not.toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByTestId('round-history')).toHaveTextContent(
      'RH:2:2:st-1'
    )
  })

  it('opens the edit-objective dialog only when the action is available', async () => {
    vi.mocked(api.study.availableActions).mockResolvedValue({
      status: 'ok', study_id: 'st-1', execution_status: 'running',
      actions: [{ name: 'replace_objective', label: '修改目标', destructive: false }],
    } as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /修改目标/ }))
    expect(screen.getByTestId('edit-objective-dialog')).toBeInTheDocument()
  })

  it('renders the StudyChat bound to the study id and actions header', async () => {
    renderPage()
    expect(await screen.findByText('CHAT:st-1')).toBeInTheDocument()
    // subtitle shows strategy + created date
    expect(screen.getByText(/策略 mom_20d/)).toBeInTheDocument()
    // back-navigation button exists
    expect(screen.getByRole('button', { name: /返回/ })).toBeInTheDocument()
  })
})
