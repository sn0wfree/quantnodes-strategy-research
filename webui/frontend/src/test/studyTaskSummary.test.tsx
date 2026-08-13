// StudyTaskSummary — selection empty state, summary loading, metrics table,
// meta rows and the detail-page link.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { StudyTaskSummary } from '../components/study/StudyTaskSummary'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        summary: vi.fn(),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Target: Stub, Clock: Stub, FolderOpen: Stub, User: Stub,
    ArrowRight: Stub, BarChart3: Stub, ExternalLink: Stub,
  }
})

import { api } from '../api/client'
const mockSummary = vi.mocked(api.study.summary)

function fixture(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    study_id: 'st-1',
    execution_status: 'running',
    current_round: 2,
    max_rounds: 5,
    objective: '动量因子研究',
    strategy_name: 'momentum',
    workspace_path: '/tmp/ws',
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
  vi.clearAllMocks()
})

describe('StudyTaskSummary', () => {
  it('shows the selection empty state without a study id', () => {
    render(
      <MemoryRouter>
        <StudyTaskSummary studyId={null} />
      </MemoryRouter>
    )
    expect(screen.getByText('选择任务查看摘要')).toBeInTheDocument()
    expect(mockSummary).not.toHaveBeenCalled()
  })

  it('loads and renders the summary for a selected study', async () => {
    mockSummary.mockResolvedValue(fixture() as never)
    render(
      <MemoryRouter>
        <StudyTaskSummary studyId="st-1" />
      </MemoryRouter>
    )
    await waitFor(() => expect(mockSummary).toHaveBeenCalledWith('st-1'))
    expect(await screen.findByText('动量因子研究')).toBeInTheDocument()
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText(/40% · 2 证据/)).toBeInTheDocument()
    expect(screen.getByText('momentum')).toBeInTheDocument()
    expect(screen.getByText('查看完整运行状况')).toBeInTheDocument()
  })

  it('links to the detail page', async () => {
    mockSummary.mockResolvedValue(fixture() as never)
    render(
      <MemoryRouter>
        <StudyTaskSummary studyId="st-1" />
      </MemoryRouter>
    )
    const link = await screen.findByRole('link', { name: /查看完整运行状况/ })
    expect(link).toHaveAttribute('href', '/study/st-1')
  })

  it('surfaces summary errors', async () => {
    mockSummary.mockRejectedValueOnce(new Error('summary failed') as never)
    render(
      <MemoryRouter>
        <StudyTaskSummary studyId="st-1" />
      </MemoryRouter>
    )
    expect(await screen.findByText(/summary failed/)).toBeInTheDocument()
  })

  it('reloads when the selected study changes', async () => {
    mockSummary.mockResolvedValue(fixture() as never)
    const { rerender } = render(
      <MemoryRouter>
        <StudyTaskSummary studyId="st-1" />
      </MemoryRouter>
    )
    await waitFor(() => expect(mockSummary).toHaveBeenCalledTimes(1))
    rerender(
      <MemoryRouter>
        <StudyTaskSummary studyId="st-2" />
      </MemoryRouter>
    )
    await waitFor(() => expect(mockSummary).toHaveBeenCalledTimes(2))
    expect(mockSummary).toHaveBeenLastCalledWith('st-2')
  })
})
