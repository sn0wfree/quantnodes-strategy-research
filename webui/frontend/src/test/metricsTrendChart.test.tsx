// MetricsTrendChart — empty state, reference lines carry the comparison
// op symbol; recharts mocked (jsdom has no canvas).

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: ({ name }: { name?: string }) => <div data-testid="line">{name}</div>,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ReferenceLine: ({
    label,
  }: {
    label?: { value?: string } | string
  }) => (
    <div data-testid="refline">
      {typeof label === 'object' ? label?.value : label}
    </div>
  ),
}))

import { MetricsTrendChart } from '../components/study/MetricsTrendChart'

const ROUNDS = [
  { round_num: 1, metrics: { calmar: 0.5, sharpe: 1.0, max_dd: -0.2 } },
  { round_num: 2, metrics: { calmar: 0.8, sharpe: 1.2, max_dd: -0.1 } },
] as never[]

describe('MetricsTrendChart', () => {
  it('shows the empty state without rounds', () => {
    render(<MetricsTrendChart rounds={[]} />)
    expect(screen.getByText('暂无轮次数据')).toBeInTheDocument()
  })

  it('renders one line per metric with known labels', () => {
    render(<MetricsTrendChart rounds={ROUNDS} />)
    const lines = screen.getAllByTestId('line').map((el) => el.textContent)
    expect(lines).toContain('Calmar')
    expect(lines).toContain('Sharpe')
    expect(lines).toContain('MaxDD')
  })

  it('target reference lines show the op symbol (>= → ≥)', () => {
    render(
      <MetricsTrendChart
        rounds={ROUNDS}
        metricTargets={[
          { name: 'calmar', op: '>=', value: 0.6 },
          { name: 'max_dd', op: '<=', value: -0.15 },
        ]}
      />,
    )
    const refs = screen.getAllByTestId('refline').map((el) => el.textContent)
    expect(refs).toContain('Calmar ≥ 0.6')
    expect(refs).toContain('MaxDD ≤ -0.15')
  })

  it('unknown ops fall through verbatim, unknown metrics get default color path', () => {
    render(
      <MetricsTrendChart
        rounds={ROUNDS}
        metricTargets={[{ name: 'custom_metric', op: '==', value: 1 }]}
      />,
    )
    expect(screen.getByTestId('refline')).toHaveTextContent('custom_metric = 1')
  })
})
