// RunDetailPage unit tests — metrics cards + equity/drawdown charts.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('../api/client', async () => {
  return {
    api: {
      run: {
        status: vi.fn(),
        equity: vi.fn(),
      },
    },
    ApiError: class extends Error {
      status: number
      constructor(status: number, message: string) {
        super(message)
        this.status = status
      }
    },
  }
})

vi.mock('../stores/system', () => ({
  useSystemStore: (selector: (s: { workspacePath: string }) => unknown) =>
    selector({ workspacePath: '/tmp/ws' }),
}))

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    ArrowLeft: Stub, FolderOpen: Stub, LineChart: Stub,
  }
})

import { api } from '../api/client'
import { RunDetailPage, computeDrawdowns, fmt } from '../components/run/RunDetailPage'

const mockStatus = vi.mocked(api.run.status)
const mockEquity = vi.mocked(api.run.equity)

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/run/mom_20d/run_0001']}>
      <Routes>
        <Route path="/run/:strategyName/:runName" element={<RunDetailPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('RunDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({
      status: 'ok',
      run: 'run_0001',
      metrics: {
        total_return: 0.25,
        sharpe: 1.5,
        calmar: 1.1,
        max_dd: -0.12,
        status: 'ok',
      },
    } as never)
    mockEquity.mockResolvedValue({
      status: 'ok',
      run: 'run_0001',
      equity: [
        { timestamp: 0, equity: 1.0 },
        { timestamp: 1, equity: 1.2 },
        { timestamp: 2, equity: 0.9 },
        { timestamp: 3, equity: 1.1 },
      ],
    } as never)
  })

  it('renders the run name, strategy and metric cards', async () => {
    renderPage()
    expect(await screen.findByText('run_0001')).toBeInTheDocument()
    expect(screen.getByText('mom_20d')).toBeInTheDocument()
    expect(screen.getByText('总收益')).toBeInTheDocument()
    expect(screen.getByText('0.2500')).toBeInTheDocument()
    expect(screen.getByText('1.5000')).toBeInTheDocument()
    expect(screen.getByText('-0.1200')).toBeInTheDocument()
  })

  it('calls both status and equity APIs with workspace context', async () => {
    renderPage()
    await screen.findByText('run_0001')
    expect(mockStatus).toHaveBeenCalledWith('/tmp/ws', 'mom_20d', 'run_0001')
    expect(mockEquity).toHaveBeenCalledWith('/tmp/ws', 'mom_20d', 'run_0001')
  })

  it('renders empty state when the run is missing', async () => {
    mockStatus.mockRejectedValueOnce(
      new (class extends Error {
        status = 404
      })('not found') as never
    )
    renderPage()
    expect(await screen.findByText('回测产物不存在')).toBeInTheDocument()
  })

  it('renders the no-data placeholder for an empty equity curve', async () => {
    mockEquity.mockResolvedValue({
      status: 'ok',
      run: 'run_0001',
      equity: [],
    } as never)
    renderPage()
    expect(await screen.findByText('净值曲线')).toBeInTheDocument()
    expect(screen.getAllByText('无净值数据').length).toBeGreaterThan(0)
  })
})

describe('RunDetailPage error handling', () => {
  it('surfaces non-404 errors as a banner', async () => {
    mockStatus.mockRejectedValueOnce(
      new (class extends Error {
        status = 500
        message = 'internal boom'
      })('internal boom') as never
    )
    renderPage()
    expect(await screen.findByText('internal boom')).toBeInTheDocument()
  })

  it('renders the run title even when only equity fails', async () => {
    mockEquity.mockRejectedValueOnce(new Error('equity down') as never)
    renderPage()
    expect(await screen.findByText('run_0001')).toBeInTheDocument()
    expect(screen.getByText('equity down')).toBeInTheDocument()
  })
})

describe('computeDrawdowns', () => {
  it('computes running-peak drawdowns', () => {
    const points = computeDrawdowns([
      { equity: 1.0 }, { equity: 1.2 }, { equity: 0.9 }, { equity: 1.1 },
    ])
    expect(points.map((p) => p.idx)).toEqual([0, 1, 2, 3])
    expect(points.map((p) => p.equity)).toEqual([1.0, 1.2, 0.9, 1.1])
    expect(points[0].drawdown).toBeCloseTo(0, 6)
    expect(points[1].drawdown).toBeCloseTo(0, 6)
    expect(points[2].drawdown).toBeCloseTo(-0.25, 6)
    expect(points[3].drawdown).toBeCloseTo(-0.0833333, 6)
  })

  it('returns an empty list for no points', () => {
    expect(computeDrawdowns([])).toEqual([])
  })

  it('handles a single point', () => {
    expect(computeDrawdowns([{ equity: 0.8 }])).toEqual([
      { idx: 0, equity: 0.8, drawdown: 0 },
    ])
  })

  it('returns zero drawdown for negative peak (degenerate input)', () => {
    const points = computeDrawdowns([{ equity: -1 }, { equity: -0.5 }])
    expect(points[0].drawdown).toBe(0)
    expect(points[1].drawdown).toBe(0)
  })
})

describe('fmt', () => {
  it('formats numbers with the given digit count', () => {
    expect(fmt(0.12345)).toBe('0.1235')
    expect(fmt(1)).toBe('1.0000')
    expect(fmt(0.5, 2)).toBe('0.50')
  })

  it('renders an em dash for nullish values', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt(undefined)).toBe('—')
  })

  it('coerces numeric strings and passes through non-finite values', () => {
    expect(fmt('0.25')).toBe('0.2500')
    expect(fmt('NaN')).toBe('NaN')
    expect(fmt('abc')).toBe('abc')
  })
})
