/**
 * Tests for StudyChat round discovery fallback (Fix B).
 *
 * discoverRoundSessions parses `study:{studyId}:round:N` session ids
 * so the round nav rail can recover when study_rounds DB rows are
 * missing (rounds crashed before finalization).
 */
import { describe, expect, it } from 'vitest'
import {
  discoverRoundSessions,
  toRoundSummaries,
} from '../components/study/dashboard/widgets/StudyChat'

describe('discoverRoundSessions', () => {
  const studyId = 'study_abc123'

  it('extracts and sorts round numbers ascending', () => {
    const sessions = [
      { id: 'study:study_abc123:round:3' },
      { id: 'study:study_abc123:round:1' },
      { id: 'study:study_abc123:round:2' },
    ]
    expect(discoverRoundSessions(sessions, studyId)).toEqual([1, 2, 3])
  })

  it('ignores other studies and non-round sessions', () => {
    const sessions = [
      { id: 'study:study_OTHER:round:9' },
      { id: 'study_abc123' }, // bare study session
      { id: 'regular-chat-session' },
      { id: 'dag:some-dag' },
    ]
    expect(discoverRoundSessions(sessions, studyId)).toEqual([])
  })

  it('ignores malformed round suffixes and non-positive numbers', () => {
    const sessions = [
      { id: 'study:study_abc123:round:abc' },
      { id: 'study:study_abc123:round:' },
      { id: 'study:study_abc123:round:0' },
      { id: 'study:study_abc123:round:-1' },
      { id: 'study:study_abc123:round:4' },
    ]
    expect(discoverRoundSessions(sessions, studyId)).toEqual([4])
  })

  it('dedupes duplicate round numbers', () => {
    const sessions = [
      { id: 'study:study_abc123:round:2' },
      { id: 'study:study_abc123:round:2' },
    ]
    expect(discoverRoundSessions(sessions, studyId)).toEqual([2])
  })

  it('handles empty session list', () => {
    expect(discoverRoundSessions([], studyId)).toEqual([])
  })
})

describe('toRoundSummaries', () => {
  it('builds minimal round summaries with defaults', () => {
    const summaries = toRoundSummaries([1, 3])
    expect(summaries).toHaveLength(2)
    expect(summaries[0]).toMatchObject({
      round_num: 1,
      run_name: '',
      metrics: null,
      verdict: null,
      created_at: '',
    })
    expect(summaries[1].round_num).toBe(3)
  })

  it('returns empty array for no rounds', () => {
    expect(toRoundSummaries([])).toEqual([])
  })
})
