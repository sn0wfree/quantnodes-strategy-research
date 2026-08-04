// Tests for the study_* SSE handlers (hooks/sse/studyHandlers.ts) —
// verify payload → useStudyStore.current merge behavior.

import { describe, it, expect, beforeEach } from 'vitest'
import {
  studyRound,
  studyCompleted,
  studyFailed,
  studyPaused,
  studyResumed,
  studyMonitoringStarted,
  studyDriftDetected,
} from '../hooks/sse/studyHandlers'
import { useStudyStore } from '../stores/study'

function ctx() {
  // Minimal SSEContext — studyHandlers only touch useStudyStore via
  // getState(), so an empty object is enough for the contract.
  return {} as never
}

describe('study SSE handlers', () => {
  beforeEach(() => {
    useStudyStore.getState().reset()
  })

  it('study_round merges metrics + round', () => {
    studyRound(
      {
        study_id: 's1',
        round: 3,
        run: 'run_0003',
        metrics: { calmar: 0.52, sharpe: 0.4 },
        verdict: 'keep',
      },
      ctx(),
    )
    const cur = useStudyStore.getState().current
    expect(cur?.study_id).toBe('s1')
    expect(cur?.current_round).toBe(3)
    expect(cur?.last_metrics).toEqual({ calmar: 0.52, sharpe: 0.4 })
    expect(cur?.last_verdict).toBe('keep')
  })

  it('study_round preserves prior fields when event lacks them', () => {
    useStudyStore.getState().setCurrent({
      status: 'ok', study_id: 's1', execution_status: 'running',
      current_round: 1, objective: '研究动量',
    })
    studyRound({ study_id: 's1', metrics: { calmar: 0.6 } }, ctx())
    const cur = useStudyStore.getState().current
    expect(cur?.objective).toBe('研究动量')
    expect(cur?.execution_status).toBe('running') // untouched
    expect(cur?.last_metrics).toEqual({ calmar: 0.6 })
  })

  it('study_completed sets execution_status', () => {
    studyCompleted({ study_id: 's1', round: 5, metrics: { calmar: 0.71 } }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('complete')
    expect(useStudyStore.getState().current?.current_round).toBe(5)
  })

  it('study_failed sets error status + message', () => {
    studyFailed({ study_id: 's1', error: 'stagnation', reason: 'max_rounds' }, ctx())
    const cur = useStudyStore.getState().current
    expect(cur?.execution_status).toBe('error')
    expect(cur?.last_error).toBe('stagnation')
  })

  it('study_paused / resumed / monitoring / drift statuses', () => {
    studyPaused({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('paused')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyResumed({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('running')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyMonitoringStarted({ study_id: 's1', interval_seconds: 3600 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('monitoring')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyDriftDetected({ study_id: 's1', metrics: { calmar: 0.2 } }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('needs_refresh')
    expect(useStudyStore.getState().current?.last_metrics).toEqual({ calmar: 0.2 })
  })
})