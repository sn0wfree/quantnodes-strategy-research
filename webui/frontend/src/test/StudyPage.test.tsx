// StudyPage smoke tests — history list rendering + no-session empty state.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        list: vi.fn(),
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
  }
})

import { api } from '../api/client'
import { StudyPage } from '../pages/StudyPage'
import { useSessionStore } from '../stores/session'
import { useStudyStore } from '../stores/study'
import { useSystemStore } from '../stores/system'

const mockList = vi.mocked(api.study.list)

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

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 's-1' } as never)
  useStudyStore.setState({ current: null, list: [], busy: false, error: '' })
  useSystemStore.setState({ workspacePath: '/ws' } as never)
  mockList.mockResolvedValue({ status: 'ok', studies: STUDIES } as never)
})

describe('StudyPage', () => {
  it('renders header + session study area + history list', async () => {
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    expect(screen.getByText('Study 研究任务')).toBeTruthy()
    expect(screen.getByText('历史研究')).toBeTruthy()
    await waitFor(() => {
      expect(screen.getByText('动量因子研究')).toBeTruthy()
      expect(screen.getByText('价值因子研究')).toBeTruthy()
    })
  })

  it('shows no-session empty state when currentSessionId is null', () => {
    useSessionStore.setState({ currentSessionId: null } as never)
    render(
      <MemoryRouter>
        <StudyPage />
      </MemoryRouter>
    )
    expect(screen.getByText('尚未选择 session')).toBeTruthy()
  })
})
