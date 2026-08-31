// AgentApprovalDialog — oldest pending approval for the study; approve /
// reject forwards to approveAgentLoop and always resolves locally.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { fakeStore } = vi.hoisted(() => ({
  fakeStore: {
    agentApprovals: {} as Record<string, unknown>,
    resolveAgentApproval: vi.fn(),
  },
}))

vi.mock('../stores/study', () => ({
  useStudyStore: (sel: (s: typeof fakeStore) => unknown) => sel(fakeStore),
}))

vi.mock('../api/client', () => ({
  api: { study: { approveAgentLoop: vi.fn() } },
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'AlertTriangle',
    'Check',
    'X',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { AgentApprovalDialog } from '../components/study/AgentApprovalDialog'

const mockApprove = vi.mocked(api.study.approveAgentLoop)

function setApproval(overrides: Record<string, unknown> = {}) {
  fakeStore.agentApprovals = {
    'st-1:researcher:3': {
      study_id: 'st-1',
      role: 'researcher',
      iteration: 3,
      tool_hash: 'abc123',
      window: 3,
      timeout_s: 600,
      on_timeout: 'continue',
      requested_at: 100,
      message: '连续 3 轮无进展',
      ...overrides,
    },
  }
}

describe('AgentApprovalDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fakeStore.resolveAgentApproval = vi.fn()
    mockApprove.mockResolvedValue({ status: 'ok' } as never)
  })

  it('renders nothing when there is no pending approval', () => {
    const { container } = render(<AgentApprovalDialog studyId="st-1" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the oldest approval for the study with details', () => {
    setApproval()
    fakeStore.agentApprovals['st-1:strategist:1'] = {
      study_id: 'st-1', role: 'strategist', iteration: 1,
      tool_hash: '', window: 3, timeout_s: 120, on_timeout: 'reject',
      requested_at: 50, message: '更早的请求',
    }
    render(<AgentApprovalDialog studyId="st-1" />)
    // requested_at 50 < 100 → strategist is oldest and wins
    expect(screen.getByText('更早的请求')).toBeInTheDocument()
    expect(screen.getByText('strategist')).toBeInTheDocument()
    // 120s timeout → 2 minutes, on_timeout reject → 中止该 Agent
    expect(screen.getByText(/2 分钟内无响应将默认 中止该 Agent/)).toBeInTheDocument()
  })

  it('approve forwards the normalized decision and resolves', async () => {
    setApproval()
    render(<AgentApprovalDialog studyId="st-1" />)
    fireEvent.click(screen.getByRole('button', { name: /批准继续/ }))
    await waitFor(() => {
      expect(mockApprove).toHaveBeenCalledWith('st-1', 'approved')
    })
    await waitFor(() => {
      expect(fakeStore.resolveAgentApproval).toHaveBeenCalledWith(
        'st-1', 'researcher', 3,
      )
    })
  })

  it('dismisses locally even when the backend call fails', async () => {
    setApproval()
    mockApprove.mockRejectedValueOnce(new Error('already timed out') as never)
    render(<AgentApprovalDialog studyId="st-1" />)
    fireEvent.click(screen.getByRole('button', { name: /中止该 Agent/ }))
    await waitFor(() => {
      expect(fakeStore.resolveAgentApproval).toHaveBeenCalled()
    })
  })

  it('ignores approvals from other studies', () => {
    setApproval({ study_id: 'st-other' })
    const { container } = render(<AgentApprovalDialog studyId="st-1" />)
    expect(container).toBeEmptyDOMElement()
  })
})
