// StudyActionMenu — lazy availableActions fetch on open, argument-UI
// actions filtered out, confirm-gated cancel/archive.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  api: { study: { availableActions: vi.fn() } },
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  const out: Record<string, unknown> = { default: Stub }
  for (const n of [
    'Archive',
    'ArchiveRestore',
    'MoreVertical',
    'Pause',
    'Play',
    'X',
  ]) out[n] = Stub
  return out
})

import { api } from '../api/client'
import { StudyActionMenu } from '../components/study/StudyActionMenu'

const mockActions = vi.mocked(api.study.availableActions)

function renderMenu() {
  const onAction = vi.fn().mockResolvedValue(undefined)
  const onRefresh = vi.fn()
  render(
    <StudyActionMenu
      study={{ study_id: 'st-1', execution_status: 'running' }}
      onAction={onAction}
      onRefresh={onRefresh}
    />,
  )
  return { onAction, onRefresh }
}

describe('StudyActionMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockActions.mockResolvedValue({
      status: 'ok',
      study_id: 'st-1',
      execution_status: 'running',
      actions: [
        { name: 'pause', label: '暂停', destructive: false },
        { name: 'replace_objective', label: '修改目标', destructive: false },
        { name: 'cancel', label: '取消', destructive: true },
      ],
    } as never)
  })

  it('fetches actions lazily — only when opened', async () => {
    renderMenu()
    expect(mockActions).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '操作' }))
    await waitFor(() => expect(mockActions).toHaveBeenCalledWith('st-1'))
    // replace_objective needs an argument UI → filtered out of the menu
    expect(await screen.findByRole('menuitem', { name: /暂停/ })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /修改目标/ })).not.toBeInTheDocument()
  })

  it('shows 暂无可用操作 when the fetch fails', async () => {
    mockActions.mockRejectedValueOnce(new Error('500') as never)
    renderMenu()
    fireEvent.click(screen.getByRole('button', { name: '操作' }))
    expect(await screen.findByText('暂无可用操作')).toBeInTheDocument()
  })

  it('cancel requires a confirmed window.confirm', async () => {
    const { onAction, onRefresh } = renderMenu()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '操作' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /取消/ }))
    expect(onAction).not.toHaveBeenCalled()
    // now accept the confirm
    confirmSpy.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: '操作' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /取消/ }))
    await waitFor(() => expect(onAction).toHaveBeenCalledWith('cancel'))
    expect(onRefresh).toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('pause dispatches without a confirm gate', async () => {
    const { onAction } = renderMenu()
    fireEvent.click(screen.getByRole('button', { name: '操作' }))
    fireEvent.click(await screen.findByRole('menuitem', { name: /暂停/ }))
    await waitFor(() => expect(onAction).toHaveBeenCalledWith('pause'))
    // menu closed after selection
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
