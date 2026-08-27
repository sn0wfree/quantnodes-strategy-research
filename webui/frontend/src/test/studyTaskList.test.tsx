// StudyTaskList — filter chips, active-first sorting, selection highlight,
// refresh button, loading skeleton and empty states.
//
// The component uses useNavigate() (double-click → detail page), so every
// render must be wrapped in a <MemoryRouter>.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { StudyTaskList } from '../components/study/StudyTaskList'
import type { StudySummary } from '../api/client'

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Clock: Stub, ArrowRight: Stub, RefreshCw: Stub, ListChecks: Stub,
    Archive: Stub, ArchiveRestore: Stub, Pause: Stub, Play: Stub,
    RotateCcw: Stub, X: Stub, MoreVertical: Stub, Info: Stub,
    default: Stub,
  }
})

function study(overrides: Partial<StudySummary> = {}): StudySummary {
  return {
    study_id: 'st-1',
    session_id: 's-1',
    objective: '动量因子研究',
    strategy_name: 'momentum',
    workspace_path: '/ws',
    execution_status: 'running',
    current_round: 2,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-01T12:00:00Z',
    ...overrides,
  }
}

function renderList(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('StudyTaskList', () => {
  it('shows the empty state without studies', () => {
    renderList(<StudyTaskList studies={[]} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText('任务列表')).toBeInTheDocument()
    expect(screen.getByText(/暂无研究任务/)).toBeInTheDocument()
  })

  it('renders task cards with objective, strategy and verdict', () => {
    renderList(
      <StudyTaskList
        studies={[study(), study({ study_id: 'st-2', objective: '价值因子研究', strategy_name: 'value', execution_status: 'complete' })]}
        selectedId={null}
        onSelect={() => {}}
      />,
    )
    expect(screen.getByText('动量因子研究')).toBeInTheDocument()
    expect(screen.getByText('价值因子研究')).toBeInTheDocument()
    expect(screen.getByText('momentum')).toBeInTheDocument()
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getAllByText('已完成').length).toBeGreaterThanOrEqual(1)
  })

  it('sorts active studies first regardless of update time', () => {
    renderList(
      <StudyTaskList
        studies={[
          study({ study_id: 'old-active', updated_at: '2026-07-01T00:00:00Z' }),
          study({ study_id: 'new-complete', objective: '价值因子研究', execution_status: 'complete', updated_at: '2026-08-10T00:00:00Z' }),
        ]}
        selectedId={null}
        onSelect={() => {}}
      />,
    )
    const cards = screen.getAllByRole('button').filter((b) => b.hasAttribute('aria-pressed'))
    expect(cards[0]).toHaveTextContent('动量因子研究')
    expect(cards[1]).toHaveTextContent('价值因子研究')
  })

  it('filters by 进行中 / 已完成 / 全部 chips', () => {
    const studies = [
      study(),
      study({ study_id: 'st-2', objective: '价值因子研究', execution_status: 'complete' }),
    ]
    renderList(<StudyTaskList studies={studies} selectedId={null} onSelect={() => {}} />)
    fireEvent.click(screen.getByText('进行中'))
    expect(screen.getByText('动量因子研究')).toBeInTheDocument()
    expect(screen.queryByText('价值因子研究')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('已完成'))
    expect(screen.queryByText('动量因子研究')).not.toBeInTheDocument()
    expect(screen.getByText('价值因子研究')).toBeInTheDocument()

    fireEvent.click(screen.getByText('全部'))
    expect(screen.getByText('动量因子研究')).toBeInTheDocument()
    expect(screen.getByText('价值因子研究')).toBeInTheDocument()
  })

  it('marks the selected card with the selected state', () => {
    const { container } = renderList(
      <StudyTaskList
        studies={[study(), study({ study_id: 'st-2' })]}
        selectedId="st-2"
        onSelect={() => {}}
      />,
    )
    const selected = container.querySelector('.border-primary-500\\/60')
    expect(selected).toBeTruthy()
    expect(selected?.textContent).toContain('动量因子研究')
  })

  it('calls onSelect when a card is clicked', () => {
    const onSelect = vi.fn()
    renderList(<StudyTaskList studies={[study()]} selectedId={null} onSelect={onSelect} />)
    fireEvent.click(screen.getByText('动量因子研究'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ study_id: 'st-1' }))
  })

  it('calls onRefresh from the refresh button', () => {
    const onRefresh = vi.fn()
    renderList(
      <StudyTaskList studies={[study()]} selectedId={null} onSelect={() => {}} onRefresh={onRefresh} />,
    )
    fireEvent.click(screen.getByTitle('刷新'))
    expect(onRefresh).toHaveBeenCalledTimes(1)
  })

  it('shows a loading skeleton instead of the empty state while loading', () => {
    const { container } = renderList(
      <StudyTaskList studies={[]} selectedId={null} loading onSelect={() => {}} />,
    )
    expect(screen.queryByText(/暂无研究任务/)).not.toBeInTheDocument()
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
  })
})
