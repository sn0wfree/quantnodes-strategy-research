// Smoke tests for the study UI (StudyCreateForm + StudyProgress + StudyTab).
// Patches the api client and zustand store to keep these unit tests fast.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// Mock api client (the real client hits fetch).
vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        start: vi.fn(),
        status: vi.fn(),
        list: vi.fn(),
        summary: vi.fn(),
        pause: vi.fn(),
        resume: vi.fn(),
        cancel: vi.fn(),
        directive: vi.fn(),
      },
    },
    ApiError: class extends Error {},
  }
})

// Stub lucide-react icons (keep tests fast + avoid SVG noise).
vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Pause: Stub, Play: Stub, X: Stub, ArrowRightCircle: Stub,
    ExternalLink: Stub, Plus: Stub, Activity: Stub, BookOpen: Stub,
    Target: Stub, Layers: Stub, Eye: Stub, EyeOff: Stub, RefreshCw: Stub,
    ArrowRight: Stub, Search: Stub, MessageSquare: Stub,
    Settings: Stub, Workflow: Stub, Bot: Stub,
    ChevronRight: Stub, ChevronDown: Stub, AlertTriangle: Stub,
    BarChart3: Stub, CheckCircle2: Stub, Circle: Stub, SlidersHorizontal: Stub,
  }
})

import { api } from '../api/client'
import { useStudyStore } from '../stores/study'
import { StudyCreateForm } from '../components/study/StudyCreateForm'
import { StudyProgress } from '../components/study/StudyProgress'
import { StudyTab } from '../components/study/StudyTab'

describe('study/stores', () => {
  beforeEach(() => {
    useStudyStore.getState().reset()
  })

  it('store tracks current + list', () => {
    const s = useStudyStore.getState()
    s.setCurrent({ status: 'ok', study_id: 's1', execution_status: 'running' })
    expect(useStudyStore.getState().current?.study_id).toBe('s1')
    s.setList([
      { study_id: 's1', session_id: 'sess', objective: 'x',
        strategy_name: 's', workspace_path: '/w',
        execution_status: 'running', current_round: 1 } as any,
    ])
    expect(useStudyStore.getState().list).toHaveLength(1)
  })

  it('reset clears all fields', () => {
    const s = useStudyStore.getState()
    s.setError('boom')
    s.setBusy(true)
    s.reset()
    const after = useStudyStore.getState()
    expect(after.current).toBeNull()
    expect(after.list).toEqual([])
    expect(after.busy).toBe(false)
    expect(after.error).toBe('')
  })
})

describe('StudyCreateForm', () => {
  beforeEach(() => {
    useStudyStore.getState().reset()
    vi.clearAllMocks()
  })

  it('shows validation error on empty objective', async () => {
    render(
      <StudyCreateForm sessionId="test-session" workspacePath="/w" />
    )
    const btn = screen.getByText('启动 study')
    // Disabled by empty objective
    expect(btn).toBeDisabled()
  })

  it('submits with objective + default metrics', async () => {
    vi.mocked(api.study.start).mockResolvedValue({
      status: 'ok', study_id: 'new_id', goal_id: 'g', execution_status: 'queued',
    })
    render(
      <StudyCreateForm sessionId="test-session" workspacePath="/w" onCreated={() => {}} />
    )
    fireEvent.change(
      screen.getByPlaceholderText(/研究 A 股动量因子/),
      { target: { value: '研究动量' } },
    )
    // Wait for strategy name auto-generation (300ms debounce + render)
    await waitFor(() => {
      const btn = screen.getByText('启动 study')
      expect(btn).not.toBeDisabled()
    }, { timeout: 2000 })
    fireEvent.click(screen.getByText('启动 study'))
    await waitFor(() => expect(api.study.start).toHaveBeenCalledTimes(1))
    const call = vi.mocked(api.study.start).mock.calls[0][0]
    expect(call.objective).toBe('研究动量')
    expect(call.workspace_path).toBe('/w')
    expect(call.strategy_name).toBeTruthy()
    expect(call.metric_targets).toEqual([
      { name: 'calmar', op: '>=', value: 0.5 },
      { name: 'sharpe', op: '>=', value: 0.3 },
      { name: 'max_dd', op: '>=', value: -0.15 },
    ])
  })

  it('surfaces api errors', async () => {
    vi.mocked(api.study.start).mockRejectedValue(new Error('bad workspace'))
    render(<StudyCreateForm sessionId="test-session" workspacePath="/w" />)
    fireEvent.change(
      screen.getByPlaceholderText(/研究 A 股动量因子/),
      { target: { value: '研究动量' } },
    )
    // Wait for strategy name auto-generation
    await waitFor(() => {
      const btn = screen.getByText('启动 study')
      expect(btn).not.toBeDisabled()
    }, { timeout: 2000 })
    fireEvent.click(screen.getByText('启动 study'))
    expect(await screen.findByText(/bad workspace/)).toBeInTheDocument()
  })
})

describe('StudyProgress', () => {
  beforeEach(() => {
    useStudyStore.getState().reset()
    vi.clearAllMocks()
  })

  it('shows no-study message when status is empty', async () => {
    vi.mocked(api.study.status).mockResolvedValue({ status: 'no_study' })
    render(<StudyProgress />)
    expect(await screen.findByText(/暂无 study/)).toBeInTheDocument()
  })

  it('renders metrics + controls', async () => {
    vi.mocked(api.study.status).mockResolvedValue({
      status: 'ok', study_id: 's1', execution_status: 'running',
      current_round: 2, objective: '研究动量',
      metric_targets: [{ name: 'calmar', op: '>=', value: 0.5 }],
      last_metrics: { calmar: 0.62, sharpe: 0.4, max_dd: -0.1 },
      last_verdict: 'keep',
      goal_snapshot: {
        goal_id: 'g1', progress_percent: 60, evidence_count: 3,
        criteria: [
          { criterion_id: 'c1', text: '定义因子', status: 'covered', required: true },
          { criterion_id: 'c2', text: '收集证据', status: 'pending', required: true },
        ],
      },
    })
    vi.mocked(api.study.summary).mockResolvedValue({
      status: 'ok', study_id: 's1', execution_status: 'running',
      current_round: 2, max_rounds: 5, objective: '研究动量',
      last_metrics: { calmar: 0.62 }, last_verdict: 'keep',
      recent_rounds: [], scoreboard: [], goal_snapshot: null,
    })
    render(<StudyProgress />)
    expect(await screen.findByText('运行中')).toBeInTheDocument()
    // Round display format is "Round X/Y"
    expect(screen.getAllByText(/Round 2/).length).toBeGreaterThan(0)
    // Objective and criteria are rendered
    expect(await screen.findByText('定义因子')).toBeInTheDocument()
    expect(screen.getByText('收集证据')).toBeInTheDocument()
    // Progress percentage is rendered
    expect(screen.getByText(/60%/)).toBeInTheDocument()
    // Verdict badge (may or may not be rendered depending on component state)
    // Pause/cancel buttons
    expect(screen.getByText('暂停')).toBeInTheDocument()
    expect(screen.getByText('取消')).toBeInTheDocument()
  })
})

describe('StudyTab', () => {
  beforeEach(() => {
    useStudyStore.getState().reset()
    vi.clearAllMocks()
  })

  it('renders create form when no active study', () => {
    vi.mocked(api.study.status).mockResolvedValue({ status: 'no_study' })
    render(
      <StudyTab sessionId="sess" workspacePath="/w" />
    )
    expect(screen.getByText('研究目标')).toBeInTheDocument()
  })

  it('switches to progress when a study is active', async () => {
    // Pre-populate the store + ensure polling doesn't overwrite.
    useStudyStore.getState().setCurrent({
      status: 'ok', study_id: 's1', execution_status: 'monitoring',
      current_round: 5,
      last_metrics: { calmar: 0.6 },
    })
    vi.mocked(api.study.status).mockResolvedValue({
      status: 'ok', study_id: 's1', execution_status: 'monitoring',
      current_round: 5,
      last_metrics: { calmar: 0.6 },
    })
    render(
      <StudyTab sessionId="sess" workspacePath="/w" />
    )
    expect(await screen.findByText('监控中')).toBeInTheDocument()
  })

  it('shows empty state without a session id', () => {
    render(
      <StudyTab
        sessionId={undefined as unknown as string}
        workspacePath="/w"
      />
    )
    expect(screen.getByText(/尚未选择 session/)).toBeInTheDocument()
  })
})