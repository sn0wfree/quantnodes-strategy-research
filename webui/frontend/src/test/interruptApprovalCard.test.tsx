// InterruptApprovalCard — HITL approve/reject against the real
// interrupt_id; status transitions pending → approved/rejected → error.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { post: vi.fn() },
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Check',
    'X',
    'Loader2',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { InterruptApprovalCard } from '../components/study/dashboard/widgets/InterruptApprovalCard'

const mockPost = vi.mocked(api.post)

describe('InterruptApprovalCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPost.mockResolvedValue({ status: 'ok' } as never)
  })

  it('renders pending state with hypothesis and message', () => {
    render(
      <InterruptApprovalCard
        studyId="st-1"
        interruptId="intr-9"
        hypothesis="低波动因子组合"
        message="novelty gate"
      />,
    )
    expect(screen.getByText(/等待审批/)).toBeInTheDocument()
    expect(screen.getByText('novelty gate')).toBeInTheDocument()
    expect(screen.getByText('低波动因子组合')).toBeInTheDocument()
  })

  it('approve posts decision approve to the REAL interrupt id', async () => {
    const onApproved = vi.fn()
    render(
      <InterruptApprovalCard
        studyId="st-1"
        interruptId="intr-9"
        onApproved={onApproved}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /批准/ }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/study/st-1/interrupts/intr-9/respond',
        { decision: 'approve' },
      )
    })
    expect(await screen.findByText('已批准')).toBeInTheDocument()
    expect(onApproved).toHaveBeenCalled()
  })

  it('reject posts decision reject and switches to rejected', async () => {
    const onRejected = vi.fn()
    render(
      <InterruptApprovalCard
        studyId="st-1"
        interruptId="intr-9"
        onRejected={onRejected}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /拒绝/ }))
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/study/st-1/interrupts/intr-9/respond',
        { decision: 'reject' },
      )
    })
    expect(await screen.findByText('已拒绝')).toBeInTheDocument()
    expect(onRejected).toHaveBeenCalled()
  })

  it('shows the error state with retry after a failed post', async () => {
    mockPost.mockRejectedValueOnce(new Error('404 no interrupt') as never)
    render(<InterruptApprovalCard studyId="st-1" interruptId="intr-404" />)
    fireEvent.click(screen.getByRole('button', { name: /批准/ }))
    expect(await screen.findByText('操作失败，请重试')).toBeInTheDocument()
    // retry returns to pending; buttons are back
    fireEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(await screen.findByRole('button', { name: /批准/ })).toBeInTheDocument()
  })
})
