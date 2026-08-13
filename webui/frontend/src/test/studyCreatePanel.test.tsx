// StudyCreatePanel — objective input, auto strategy-name preview, collapsed
// advanced params, start submit + session prompt.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { StudyCreatePanel } from '../components/study/StudyCreatePanel'
import { useStudyStore } from '../stores/study'
import { useAuthStore } from '../stores/auth'

vi.mock('../api/client', async () => {
  return {
    api: {
      study: {
        start: vi.fn(),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Plus: Stub, X: Stub, Send: Stub, SlidersHorizontal: Stub,
    ChevronDown: Stub, ChevronRight: Stub, RefreshCw: Stub,
  }
})

import { api } from '../api/client'
const mockStart = vi.mocked(api.study.start)

beforeEach(() => {
  useStudyStore.setState({ busy: false, error: '' })
  useAuthStore.setState({ user: { username: 'tester' } } as never)
  vi.clearAllMocks()
})

describe('StudyCreatePanel', () => {
  it('shows a session prompt without a session', () => {
    render(<StudyCreatePanel sessionId={undefined} workspacePath="/w" />)
    expect(screen.getByText('尚未选择 session')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /启动 study/ })).not.toBeInTheDocument()
  })

  it('renders objective input and strategy name preview', () => {
    render(<StudyCreatePanel sessionId="sess-1" workspacePath="/w" />)
    expect(screen.getByPlaceholderText(/研究 A 股动量因子/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/自动生成/)).toBeInTheDocument()
    expect(screen.getByText('高级参数')).toBeInTheDocument()
  })

  it('disables submit with an empty objective', () => {
    render(<StudyCreatePanel sessionId="sess-1" workspacePath="/w" />)
    const btn = screen.getByRole('button', { name: /启动 study/ })
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('collapses and expands advanced params', () => {
    render(<StudyCreatePanel sessionId="sess-1" workspacePath="/w" />)
    expect(screen.queryByText('验收指标')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('高级参数'))
    expect(screen.getByText('验收指标')).toBeInTheDocument()
    expect(screen.getByText('轮数预算 (turns)')).toBeInTheDocument()
  })

  it('submits with objective + strategy name and reports the new study id', async () => {
    mockStart.mockResolvedValue({
      status: 'ok', study_id: 'st-new', execution_status: 'queued',
    } as never)
    const onCreated = vi.fn()
    render(<StudyCreatePanel sessionId="sess-1" workspacePath="/w" onCreated={onCreated} />)
    fireEvent.change(screen.getByPlaceholderText(/研究 A 股动量因子/), {
      target: { value: '动量因子研究' },
    })
    fireEvent.change(screen.getByPlaceholderText(/自动生成/), {
      target: { value: 'momentum_20d' },
    })
    fireEvent.click(screen.getByRole('button', { name: /启动 study/ }))
    await waitFor(() => expect(mockStart).toHaveBeenCalled())
    expect(mockStart).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: 'sess-1',
        objective: '动量因子研究',
        strategy_name: 'momentum_20d',
        workspace_path: '/w',
      })
    )
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('st-new'))
  })

  it('surfaces start errors', async () => {
    mockStart.mockRejectedValueOnce(new Error('start failed') as never)
    render(<StudyCreatePanel sessionId="sess-1" workspacePath="/w" />)
    fireEvent.change(screen.getByPlaceholderText(/研究 A 股动量因子/), {
      target: { value: 'x' },
    })
    fireEvent.change(screen.getByPlaceholderText(/自动生成/), {
      target: { value: 'x_1' },
    })
    fireEvent.click(screen.getByRole('button', { name: /启动 study/ }))
    expect(await screen.findByText(/start failed/)).toBeInTheDocument()
  })
})
