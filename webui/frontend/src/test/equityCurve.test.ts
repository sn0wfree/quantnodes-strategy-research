import { describe, it, expect } from 'vitest'
import {
  extractEquityCurve,
  extractLatestBacktestMetrics,
} from '../utils/equityCurve'
import type { Message, MessagePart } from '../stores/chat'

function makeMsg(ts: number, parts: MessagePart[]): Message {
  return { id: `m${ts}`, session_id: 's1', role: 'assistant', parts, created_at: ts }
}

describe('extractEquityCurve', () => {
  it('returns null when there are no messages', () => {
    expect(extractEquityCurve([])).toBeNull()
  })

  it('returns null when no line chart matches', () => {
    const messages = [
      makeMsg(1, [{ type: 'chart', chart_type: 'bar', data: [{ name: 'a', v: 1 }], title: '收益分布' }]),
      makeMsg(2, [{ type: 'chart', chart_type: 'line', data: [{ name: 'a', v: 1 }], title: '因子IC' }]),
    ]
    expect(extractEquityCurve(messages)).toBeNull()
  })

  it('decodes the latest equity line chart, picking the numeric field as y', () => {
    const messages = [
      makeMsg(1, [{
        type: 'chart', chart_type: 'line',
        title: '净值曲线',
        data: [
          { date: '2024-01-01', nav: 1.0 },
          { date: '2024-01-02', nav: 1.05 },
          { date: '2024-01-03', nav: 1.02 },
        ],
      }]),
    ]
    const curve = extractEquityCurve(messages)
    expect(curve).not.toBeNull()
    expect(curve!.title).toBe('净值曲线')
    expect(curve!.points).toEqual([
      { label: '2024-01-01', value: 1.0 },
      { label: '2024-01-02', value: 1.05 },
      { label: '2024-01-03', value: 1.02 },
    ])
  })

  it('prefers the most recent qualifying chart', () => {
    const messages = [
      makeMsg(3, [{ type: 'chart', chart_type: 'line', title: 'equity', data: [{ t: 'b', v: 2 }] }]),
      makeMsg(1, [{ type: 'chart', chart_type: 'line', title: 'nav', data: [{ t: 'a', v: 1 }] }]),
    ]
    const curve = extractEquityCurve(messages)
    expect(curve!.points).toEqual([{ label: 'b', value: 2 }])
  })

  it('skips empty or non-object data rows', () => {
    const messages = [
      makeMsg(1, [{
        type: 'chart', chart_type: 'line', title: '净值',
        data: [{ t: 'a', v: 1 }, null, { t: 'b', v: NaN }, { t: 'c', v: 3 }],
      }]),
    ]
    const curve = extractEquityCurve(messages)
    expect(curve!.points).toEqual([
      { label: 'a', value: 1 },
      { label: 'c', value: 3 },
    ])
  })

  it('returns null when a matching chart has no decodable points', () => {
    const messages = [
      makeMsg(1, [{ type: 'chart', chart_type: 'line', title: '净值', data: [] }]),
    ]
    expect(extractEquityCurve(messages)).toBeNull()
  })

  it('uses row index as label when a single numeric key exists', () => {
    const messages = [
      makeMsg(1, [{
        type: 'chart', chart_type: 'line', title: '净值曲线',
        data: [{ v: 1.0 }, { v: 1.1 }, { v: 1.2 }],
      }]),
    ]
    const curve = extractEquityCurve(messages)
    expect(curve!.points.map((p) => p.value)).toEqual([1.0, 1.1, 1.2])
    expect(curve!.points.map((p) => p.label)).toEqual(['0', '1', '2'])
  })
})

describe('extractLatestBacktestMetrics', () => {
  it('returns null when no run_backtest tool_call is present', () => {
    expect(extractLatestBacktestMetrics([])).toBeNull()
    const messages: Message[] = [
      makeMsg(1, [{ type: 'tool_call', id: 't1', name: 'list_strategies', arguments: '{}', status: 'done' }]),
    ]
    expect(extractLatestBacktestMetrics(messages)).toBeNull()
  })

  it('decodes metrics from the latest run_backtest result', () => {
    const result = JSON.stringify({
      run: 'momentum_v3_20260101',
      strategy: 'momentum',
      metrics: {
        total_return: 0.123,
        sharpe: 1.45,
        max_drawdown: -0.082,
        annual_return: 0.18,
        win_rate: 0.56,
      },
      status: 'success',
    })
    const messages: Message[] = [
      makeMsg(1, [
        {
          type: 'tool_call',
          id: 't1',
          name: 'run_backtest',
          arguments: '{"strategy_name":"momentum"}',
          status: 'done',
          result,
        },
      ]),
    ]
    const m = extractLatestBacktestMetrics(messages)
    expect(m).not.toBeNull()
    expect(m!.total_return).toBeCloseTo(0.123)
    expect(m!.sharpe).toBeCloseTo(1.45)
    expect(m!.max_drawdown).toBeCloseTo(-0.082)
    expect(m!.run).toBe('momentum_v3_20260101')
    expect(m!.strategy).toBe('momentum')
  })

  it('picks the most recent run_backtest when several are present', () => {
    const older = JSON.stringify({ run: 'old', strategy: 's1', metrics: { total_return: 0.05 } })
    const newer = JSON.stringify({ run: 'new', strategy: 's2', metrics: { total_return: 0.10 } })
    const messages: Message[] = [
      makeMsg(1, [{ type: 'tool_call', id: 'a', name: 'run_backtest', arguments: '{}', status: 'done', result: older }]),
      makeMsg(2, [{ type: 'tool_call', id: 'b', name: 'run_backtest', arguments: '{}', status: 'done', result: newer }]),
    ]
    const m = extractLatestBacktestMetrics(messages)
    expect(m!.run).toBe('new')
    expect(m!.total_return).toBeCloseTo(0.10)
  })

  it('handles a markdown-fenced JSON payload', () => {
    const fenced = '```json\n{"run":"r","strategy":"s","metrics":{"total_return":0.2}}\n```'
    const messages: Message[] = [
      makeMsg(1, [{ type: 'tool_call', id: 'a', name: 'run_backtest', arguments: '{}', status: 'done', result: fenced }]),
    ]
    const m = extractLatestBacktestMetrics(messages)
    expect(m!.total_return).toBeCloseTo(0.2)
    expect(m!.run).toBe('r')
  })

  it('returns null when result is unparseable', () => {
    const messages: Message[] = [
      makeMsg(1, [{ type: 'tool_call', id: 'a', name: 'run_backtest', arguments: '{}', status: 'done', result: 'not json' }]),
    ]
    expect(extractLatestBacktestMetrics(messages)).toBeNull()
  })

  it('skips non-string result payloads', () => {
    const messages: Message[] = [
      makeMsg(1, [{ type: 'tool_call', id: 'a', name: 'run_backtest', arguments: '{}', status: 'done', result: { already: 'parsed' } as unknown as string }]),
    ]
    expect(extractLatestBacktestMetrics(messages)).toBeNull()
  })
})
describe('extractLatestBacktestMetrics — run_backtest v1.2.0 shape', () => {
  it('maps extended_metrics keys (ann_return) into the display model', () => {
    const result = JSON.stringify({
      status: 'ok',
      run: 'run_0002',
      strategy: 'a_share_momentum_v4',
      metrics: {
        ann_return: 0.12758626373180193,
        ann_vol: 0.1389137544534479,
        sharpe: 0.9184566656756659,
        max_drawdown: -0.14,
        calmar: 0.9,
        sortino: 1.1,
        win_rate: 0.42,
      },
    })
    const messages: Message[] = [
      makeMsg(1, [
        {
          type: 'tool_call',
          id: 't1',
          name: 'run_backtest',
          arguments: '{"strategy_name":"a_share_momentum_v4"}',
          status: 'done',
          result,
        },
      ]),
    ]
    const m = extractLatestBacktestMetrics(messages)
    expect(m).not.toBeNull()
    expect(m!.total_return).toBeCloseTo(0.1276, 4)
    expect(m!.annual_return).toBeCloseTo(0.1276, 4)
    expect(m!.sharpe).toBeCloseTo(0.9185, 4)
    expect(m!.max_drawdown).toBeCloseTo(-0.14)
    expect(m!.win_rate).toBeCloseTo(0.42)
    expect(m!.run).toBe('run_0002')
    expect(m!.strategy).toBe('a_share_momentum_v4')
  })
})
