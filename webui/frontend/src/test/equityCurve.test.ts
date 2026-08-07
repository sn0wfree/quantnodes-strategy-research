import { describe, it, expect } from 'vitest'
import { extractEquityCurve } from '../utils/equityCurve'
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