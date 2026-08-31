// StudyChatComposer — directive submit via button and Cmd/Ctrl+Enter,
// ok/err hints, empty input guard.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { study: { directive: vi.fn() } },
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Send',
    'Check',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { StudyChatComposer } from '../components/study/dashboard/widgets/StudyChatComposer'

const mockDirective = vi.mocked(api.study.directive)

describe('StudyChatComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockDirective.mockResolvedValue({
      status: 'ok', study_id: 'st-1', directive_id: 'd-1',
      created_at: '2026-08-01T00:00:00',
    } as never)
  })

  it('submit button is disabled for empty / whitespace-only input', () => {
    render(<StudyChatComposer studyId="st-1" />)
    const btn = screen.getByRole('button', { name: /提交/ })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '   ' },
    })
    expect(btn).toBeDisabled()
  })

  it('submits trimmed directive and shows the ok hint', async () => {
    render(<StudyChatComposer studyId="st-1" />)
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: '  试试反转因子  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /提交/ }))
    await waitFor(() => {
      expect(mockDirective).toHaveBeenCalledWith('st-1', '试试反转因子', 'webui')
    })
    expect(await screen.findByText('已提交，下轮生效')).toBeInTheDocument()
    // input cleared after submit
    expect(screen.getByRole('textbox')).toHaveValue('')
  })

  it('Cmd+Enter submits', async () => {
    render(<StudyChatComposer studyId="st-1" />)
    const ta = screen.getByRole('textbox')
    fireEvent.change(ta, { target: { value: '加大动量窗口' } })
    fireEvent.keyDown(ta, { key: 'Enter', metaKey: true })
    await waitFor(() => {
      expect(mockDirective).toHaveBeenCalledWith('st-1', '加大动量窗口', 'webui')
    })
  })

  it('shows the error hint when the API fails', async () => {
    mockDirective.mockRejectedValueOnce(new Error('session busy') as never)
    render(<StudyChatComposer studyId="st-1" />)
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'x' },
    })
    fireEvent.click(screen.getByRole('button', { name: /提交/ }))
    expect(await screen.findByText(/提交失败: session busy/)).toBeInTheDocument()
  })
})
