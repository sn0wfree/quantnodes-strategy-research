// ContinueDialog — three modes with backend-semantics alignment:
// paused/interrupted are resume-only; resume on restartable statuses
// carries from_round; custom round passes from_round.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Play',
    'RotateCcw',
    'X',
  ]) out[n] = Stub
  return out
})

import { ContinueDialog } from '../components/study/ContinueDialog'

function makeSummary(execution_status: string, current_round = 3) {
  return {
    status: 'ok',
    study_id: 'st-1',
    execution_status,
    current_round,
  } as never
}

function setup(status: string, currentRound?: number) {
  const onContinue = vi.fn()
  const onClose = vi.fn()
  render(
    <ContinueDialog
      open
      summary={makeSummary(status, currentRound)}
      onClose={onClose}
      onContinue={onContinue}
    />,
  )
  return { onContinue, onClose }
}

describe('ContinueDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('paused study: resume-only UI, submit dispatches resume without from_round', () => {
    const { onContinue } = setup('paused', 3)
    expect(screen.getByText(/只能从当前轮次（Round 3）恢复/)).toBeInTheDocument()
    // restart + custom radios are disabled
    const radios = screen.getAllByRole('radio')
    expect(radios[1]).toBeDisabled()
    expect(radios[2]).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /继续执行/ }))
    expect(onContinue).toHaveBeenCalledWith('resume')
  })

  it('restartable status: default restart dispatches bare restart', () => {
    const { onContinue } = setup('error', 4)
    expect(screen.getByText('错误')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /继续执行/ }))
    expect(onContinue).toHaveBeenCalledWith('restart')
  })

  it('resume mode on a restartable status carries from_round (高-2)', () => {
    const { onContinue } = setup('error', 4)
    fireEvent.click(screen.getAllByRole('radio')[0]) // 从当前轮次继续
    fireEvent.click(screen.getByRole('button', { name: /继续执行/ }))
    expect(onContinue).toHaveBeenCalledWith('resume', 4)
  })

  it('custom round dispatches restart with from_round', () => {
    const { onContinue } = setup('cancelled', 2)
    const customInput = screen.getByPlaceholderText('轮次') as HTMLInputElement
    fireEvent.change(customInput, { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /继续执行/ }))
    expect(onContinue).toHaveBeenCalledWith('restart', 2)
  })

  it('cancel closes without dispatching', () => {
    const { onContinue, onClose } = setup('paused', 1)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onContinue).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('renders nothing when closed', () => {
    const { container } = render(
      <ContinueDialog
        open={false}
        summary={makeSummary('paused')}
        onClose={vi.fn()}
        onContinue={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
