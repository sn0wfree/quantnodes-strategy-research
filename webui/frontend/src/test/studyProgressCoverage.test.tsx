// StudyProgress — additional coverage for the execution_status branches
// (interrupted / complete / cancelled / error / needs_refresh) and the
// last_error + view-details link rendering.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useStudyStore } from '../stores/study'

vi.mock('../api/client', async () => ({
  api: {
    study: {
      status: vi.fn(),
      summary: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      cancel: vi.fn(),
      directive: vi.fn(),
    },
  },
  ApiError: class extends Error {},
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Pause: Stub, Play: Stub, X: Stub, ArrowRightCircle: Stub,
    ExternalLink: Stub, Plus: Stub, Activity: Stub, BookOpen: Stub,
    Target: Stub, Layers: Stub, Eye: Stub, EyeOff: Stub, RefreshCw: Stub,
    ArrowRight: Stub, Search: Stub, MessageSquare: Stub, Settings: Stub,
    Workflow: Stub, Bot: Stub, ChevronRight: Stub, ChevronDown: Stub,
    AlertTriangle: Stub, BarChart3: Stub, CheckCircle2: Stub,
    SlidersHorizontal: Stub, Circle: Stub,
  }
})

import { api } from '../api/client'
import { StudyProgress } from '../components/study/StudyProgress'

const mockStatus = vi.mocked(api.study.status)
const mockSummary = vi.mocked(api.study.summary)

function setupStudy(overrides: Record<string, unknown>) {
  useStudyStore.getState().setCurrent({
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 1,
    objective: '找 alpha',
    ...overrides,
  })
}

function mockStudySuccess(extras: Record<string, unknown> = {}) {
  mockStatus.mockResolvedValue({
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 1,
    objective: 'find alpha',
    ...extras,
  } as never)
  mockSummary.mockResolvedValue({
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 1,
    max_rounds: 5,
    objective: 'find alpha',
    recent_rounds: [],
    scoreboard: [],
    goal_snapshot: null,
    ...extras,
  } as never)
}

describe('StudyProgress execution-status branches', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useStudyStore.getState().reset()
  })

  it('renders the "interrupted" status label and "继续运行" action', async () => {
    setupStudy({ execution_status: 'interrupted', current_round: 2 })
    mockStudySuccess({ execution_status: 'interrupted', current_round: 2 })
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    expect(await screen.findByText('继续运行')).toBeInTheDocument()
    // Cancel should be hidden for interrupted (only paused/monitoring/running show it)
    expect(screen.queryByText('取消')).toBeNull()
  })

  it('hides cancel for terminal "complete" status', async () => {
    setupStudy({ execution_status: 'complete' })
    mockStudySuccess({ execution_status: 'complete' })
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    expect(await screen.findByText('已完成')).toBeInTheDocument()
    expect(screen.queryByText('取消')).toBeNull()
  })

  it('hides cancel for terminal "cancelled" status', async () => {
    setupStudy({ execution_status: 'cancelled' })
    mockStudySuccess({ execution_status: 'cancelled' })
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    expect(await screen.findByText('已取消')).toBeInTheDocument()
    expect(screen.queryByText('取消')).toBeNull()
  })

  it('hides cancel for terminal "error" status', async () => {
    setupStudy({ execution_status: 'error', last_error: 'risk budget exceeded' })
    mockStudySuccess({ execution_status: 'error', last_error: 'risk budget exceeded' })
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    expect(await screen.findByText('错误')).toBeInTheDocument()
    expect(screen.getByText('risk budget exceeded')).toBeInTheDocument()
    expect(screen.queryByText('取消')).toBeNull()
  })

  it('renders the "查看详细" link to /study/{id}', async () => {
    mockStatus.mockResolvedValue({
      status: 'ok',
      study_id: 'st-detail',
      execution_status: 'running',
      current_round: 1,
      objective: 'x',
    } as never)
    mockSummary.mockResolvedValue({
      status: 'ok',
      study_id: 'st-detail',
      execution_status: 'running',
      current_round: 1,
      max_rounds: 5,
      objective: 'x',
      recent_rounds: [],
      scoreboard: [],
      goal_snapshot: null,
    } as never)
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    const link = await screen.findByText('查看详细')
    expect(link).toHaveAttribute('href', '/study/st-detail')
  })

  it('renders the last_error in rose color when present', async () => {
    setupStudy({ execution_status: 'error', last_error: 'phase timeout' })
    mockStudySuccess({ execution_status: 'error', last_error: 'phase timeout' })
    render(<StudyProgress sessionId="sess" pollIntervalMs={50} />)
    const err = await screen.findByText('phase timeout')
    // Color lives on the wrapping banner div (text-rose-300); the text
    // itself is an inner span.
    const banner = err.closest('.border-rose-800')
    expect(banner).toBeTruthy()
  })
})