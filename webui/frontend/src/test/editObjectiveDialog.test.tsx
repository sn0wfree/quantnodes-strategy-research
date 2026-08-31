// EditObjectiveDialog — validation (10–2000, must change, goalId
// required), submit forwards to replaceObjective with trimmed reason.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { study: { replaceObjective: vi.fn() } },
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Edit3',
    'Loader2',
    'X',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { EditObjectiveDialog } from '../components/study/EditObjectiveDialog'

const mockReplace = vi.mocked(api.study.replaceObjective)

function setup(overrides: Record<string, unknown> = {}) {
  const onClose = vi.fn()
  const onSuccess = vi.fn()
  render(
    <EditObjectiveDialog
      studyId="st-1"
      currentObjective="原始目标文本"
      goalId="g-1"
      open
      onClose={onClose}
      onSuccess={onSuccess}
      {...overrides}
    />,
  )
  return { onClose, onSuccess }
}

describe('EditObjectiveDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockReplace.mockResolvedValue({ status: 'ok' } as never)
  })

  it('submit is disabled until the text changes', async () => {
    setup()
    const submit = screen.getByRole('button', { name: /提交修改/ })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText(/低估值反转因子选股/), {
      target: { value: '新的研究方向，反转因子选股' },
    })
    expect(submit).toBeEnabled()
  })

  it('rejects objectives shorter than 10 chars with a hint', () => {
    setup()
    const ta = screen.getByPlaceholderText(/低估值反转因子选股/)
    fireEvent.change(ta, { target: { value: '太短' } })
    expect(screen.getByText('目标长度需在 10–2000 字之间')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /提交修改/ })).toBeDisabled()
  })

  it('shows the no-goalId warning and keeps submit disabled', () => {
    setup({ goalId: null })
    expect(
      screen.getByText(/尚未关联 goal ledger，无法执行目标修改/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /提交修改/ })).toBeDisabled()
  })

  it('submits trimmed objective + reason and closes', async () => {
    const { onClose, onSuccess } = setup()
    fireEvent.change(screen.getByPlaceholderText(/低估值反转因子选股/), {
      target: { value: '  反转因子 + 行业中性  ' },
    })
    fireEvent.change(screen.getByPlaceholderText(/最近回测显示动量失效/), {
      target: { value: '  动量失效  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /提交修改/ }))
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        'st-1', '反转因子 + 行业中性', 'g-1', '动量失效',
      )
    })
    expect(onSuccess).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('surfaces API errors and keeps the dialog open', async () => {
    mockReplace.mockRejectedValueOnce(new Error('objective conflict') as never)
    const { onClose } = setup()
    fireEvent.change(screen.getByPlaceholderText(/低估值反转因子选股/), {
      target: { value: '新的研究方向，反转因子选股' },
    })
    fireEvent.click(screen.getByRole('button', { name: /提交修改/ }))
    expect(await screen.findByText('objective conflict')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})
