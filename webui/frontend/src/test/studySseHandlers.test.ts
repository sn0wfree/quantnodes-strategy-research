// Tests for the study_* SSE handlers (hooks/sse/studyHandlers.ts) —
// verify payload → useStudyStore.current merge behavior.

import { describe, it, expect, beforeEach } from 'vitest'
import {
  studyRound,
  studyCompleted,
  studyFailed,
  studyPaused,
  studyResumed,
  studyInterrupted,
  studyCancelled,
  studyStarted,
  studyQueued,
  studyBudgetLimited,
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

  it('study_paused / resumed / interrupted / monitoring / drift statuses', () => {
    studyPaused({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('paused')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyResumed({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('running')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyInterrupted({ study_id: 's1', round: 3, reason: 'server restart' }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('interrupted')
    expect(useStudyStore.getState().current?.current_round).toBe(3)

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyMonitoringStarted({ study_id: 's1', interval_seconds: 3600 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('monitoring')

    useStudyStore.getState().setCurrent({ status: 'ok', study_id: 's1' })
    studyDriftDetected({ study_id: 's1', metrics: { calmar: 0.2 } }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('needs_refresh')
    expect(useStudyStore.getState().current?.last_metrics).toEqual({ calmar: 0.2 })
  })

  it('study_cancelled sets cancelled status', () => {
    studyCancelled({ study_id: 's1' }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('cancelled')
  })

  it('study_started sets running status', () => {
    studyStarted({ study_id: 's1', round: 1 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('running')
    expect(useStudyStore.getState().current?.current_round).toBe(1)
  })

  it('study_queued sets queued status', () => {
    studyQueued({ study_id: 's1', session_id: 'sess', objective: 'test' }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('queued')
  })

  it('study_budget_limited sets budget_limited status', () => {
    studyBudgetLimited({ study_id: 's1', used: 1000 }, ctx())
    expect(useStudyStore.getState().current?.execution_status).toBe('budget_limited')
  })

  // ── F4: current_round is monotonic — stale/replayed events must not
  // regress it ──
  it('study_round never regresses current_round (stale replay)', () => {
    useStudyStore.getState().setCurrent({
      status: 'ok', study_id: 's1', execution_status: 'running',
      current_round: 5, objective: 'x',
    })
    studyRound({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().current?.current_round).toBe(5)
    // A genuinely newer round still advances.
    studyRound({ study_id: 's1', round: 6 }, ctx())
    expect(useStudyStore.getState().current?.current_round).toBe(6)
  })

  // ── F2: study_paused with the REAL interrupt_id opens the HITL card
  // slot; resume closes it ──
  it('study_paused captures interrupt_id + hypothesis; resumed clears', () => {
    studyPaused(
      {
        study_id: 's1', round: 4, reason: 'hitl_approval',
        interrupt_id: 'int-123', hypothesis: 'momentum works',
      },
      ctx(),
    )
    const hitl = useStudyStore.getState().hitlInterrupt
    expect(hitl).not.toBeNull()
    expect(hitl?.interrupt_id).toBe('int-123')
    expect(hitl?.hypothesis).toBe('momentum works')
    expect(hitl?.round).toBe(4)

    studyResumed({ study_id: 's1', round: 4 }, ctx())
    expect(useStudyStore.getState().hitlInterrupt).toBeNull()
  })

  it('study_paused WITHOUT interrupt_id does not open the card', () => {
    studyPaused({ study_id: 's1', round: 2 }, ctx())
    expect(useStudyStore.getState().hitlInterrupt).toBeNull()
  })
})