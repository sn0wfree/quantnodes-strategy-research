import { describe, it, expect } from 'vitest'
import {
  extractFileChanges,
  extractToolActivity,
  extractBacktestResults,
  extractStrategyFiles,
  DEFAULT_EXTRACTOR_CONFIG,
} from '../utils/contextExtractors'
import type { Message, MessagePart } from '../stores/chat'

function makeMsg(id: string, ts: number, parts: MessagePart[]): Message {
  return {
    id,
    session_id: 's1',
    role: 'assistant',
    parts,
    created_at: ts,
  }
}

describe('contextExtractors', () => {
  describe('extractFileChanges', () => {
    it('returns file edits, deduplicated by path with latest timestamp', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'file_edit', file_path: 'a.py', old_content: 'old', new_content: 'new1',
        }]),
        makeMsg('m2', 2, [{
          type: 'file_edit', file_path: 'a.py', old_content: 'old', new_content: 'new2',
        }]),
        makeMsg('m3', 3, [{
          type: 'file_edit', file_path: 'b.py', old_content: '', new_content: 'init',
        }]),
      ]
      const files = extractFileChanges(messages)
      expect(files.map((f) => f.path)).toEqual(['b.py', 'a.py'])
      expect(files[1].timestamp).toBe(2)
    })

    it('skips messages with no file_edit parts', () => {
      const messages = [
        makeMsg('m1', 1, [{ type: 'text', id: 't1', text: 'no edit' }]),
      ]
      expect(extractFileChanges(messages)).toEqual([])
    })
  })

  describe('extractToolActivity', () => {
    it('respects toolLimit and sorts by recency', () => {
      const messages: Message[] = []
      for (let i = 0; i < 12; i++) {
        messages.push(makeMsg(`m${i}`, i, [{
          type: 'tool_call',
          id: `tc${i}`,
          name: 'list_files',
          arguments: `path=${i}`,
          status: 'done',
        }]))
      }
      const tools = extractToolActivity(messages, { ...DEFAULT_EXTRACTOR_CONFIG, toolLimit: 5 })
      expect(tools).toHaveLength(5)
      expect(tools[0].id).toBe('tc11')  // newest
      expect(tools[4].id).toBe('tc7')
    })

    it('prefers result over arguments when tool is done', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'tool_call',
          id: 'tc1',
          name: 'run_backtest',
          arguments: '{"strategy":"x"}',
          result: 'sharpe=1.5 return=20%',
          status: 'done',
        }]),
      ]
      const [t] = extractToolActivity(messages)
      expect(t.preview).toContain('sharpe')
    })

    it('truncates preview to configured max chars', () => {
      const long = 'x'.repeat(200)
      const messages = [
        makeMsg('m1', 1, [{
          type: 'tool_call',
          id: 'tc1',
          name: 't',
          arguments: long,
          status: 'running',
        }]),
      ]
      const [t] = extractToolActivity(messages, {
        ...DEFAULT_EXTRACTOR_CONFIG,
        toolPreviewMaxChars: 10,
      })
      expect(t.preview.length).toBeLessThanOrEqual(11)  // 10 + ellipsis
    })
  })

  describe('extractBacktestResults', () => {
    it('matches by configured title patterns', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'chart',
          chart_type: 'line',
          data: [],
          title: '回测净值曲线',
        }]),
        makeMsg('m2', 2, [{
          type: 'chart',
          chart_type: 'bar',
          data: [],
          title: 'Annual return by sector',
        }]),
      ]
      const results = extractBacktestResults(messages)
      expect(results).toHaveLength(1)
      expect(results[0].title).toBe('回测净值曲线')
    })

    it('extracts a metric label from table headers', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'table',
          headers: ['sharpe', 'return'],
          rows: [['1.5', '20%']],
          caption: 'backtest summary',
        }]),
      ]
      const [r] = extractBacktestResults(messages)
      expect(r.metrics).toEqual([{ label: 'sharpe', value: '1.5' }])
      expect(r.chartType).toBe('table')
    })

    it('respects custom backtest patterns', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'chart',
          chart_type: 'line',
          data: [],
          title: 'PnL curve',
        }]),
      ]
      // 'pnl' is in defaults; with custom config that excludes it, no match.
      const results = extractBacktestResults(messages, {
        ...DEFAULT_EXTRACTOR_CONFIG,
        backtestTitlePatterns: ['sharpe'],
      })
      expect(results).toEqual([])
    })
  })

  describe('extractStrategyFiles', () => {
    it('collects strategy files, latest content per path', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'file_edit',
          file_path: 'strategies/foo/strategy.py',
          old_content: 'old',
          new_content: 'new1',
        }]),
        makeMsg('m2', 2, [{
          type: 'file_edit',
          file_path: 'strategies/foo/strategy.py',
          old_content: 'old',
          new_content: 'new2',
        }]),
        makeMsg('m3', 3, [{
          type: 'file_edit',
          file_path: 'strategies/foo/config.yaml',
          old_content: '',
          new_content: 'name: foo',
        }]),
      ]
      const files = extractStrategyFiles(messages)
      expect(files).toHaveLength(2)
      // newest first
      expect(files[0].path).toBe('strategies/foo/config.yaml')
      const py = files.find((f) => f.path === 'strategies/foo/strategy.py')
      expect(py?.new_content).toBe('new2')
    })

    it('infers status from old_content empty → created', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'file_edit',
          file_path: 'strategies/foo/config.yaml',
          old_content: '',
          new_content: 'name: foo',
        }]),
        makeMsg('m2', 2, [{
          type: 'file_edit',
          file_path: 'strategies/foo/strategy.py',
          old_content: 'old',
          new_content: 'new',
        }]),
      ]
      const files = extractStrategyFiles(messages)
      const config = files.find((f) => f.path.endsWith('config.yaml'))
      const strategy = files.find((f) => f.path.endsWith('strategy.py'))
      expect(config?.status).toBe('created')
      expect(strategy?.status).toBe('modified')
    })

    it('ignores file_edit parts outside strategies/', () => {
      const messages = [
        makeMsg('m1', 1, [{
          type: 'file_edit',
          file_path: 'data/notes.txt',
          old_content: '',
          new_content: 'hello',
        }]),
        makeMsg('m2', 2, [{
          type: 'file_edit',
          file_path: 'strategies/foo/strategy.py',
          old_content: '',
          new_content: 'PARAMS = {}',
        }]),
      ]
      const files = extractStrategyFiles(messages)
      expect(files).toHaveLength(1)
      expect(files[0].path).toBe('strategies/foo/strategy.py')
    })
  })
})