/**
 * Tests for StudyChat session id consistency between the ChatSessionProvider,
 * chatStore.loadMessages calls, and SSE-injected Message objects.
 *
 * Bug history: StudyChat used a hard-coded "stream" sessionId for the
 * ChatSessionProvider while loadMessages loaded "study:{id}:round:{N}".
 * MessageList filters `m.session_id === currentSessionId`, so every
 * loaded message was dropped and the page rendered only the empty state.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  buildAgentEventMessage,
  buildEventMessage,
} from '../components/study/dashboard/widgets/StudyChat'

const STUDY_CHAT_SRC = readFileSync(
  resolve(__dirname, '../components/study/dashboard/widgets/StudyChat.tsx'),
  'utf8',
)

describe('StudyChat session id consistency', () => {
  describe('buildEventMessage (study events)', () => {
    it('uses study:{id}:round:{N} session_id (matches loadMessages)', () => {
      const m = buildEventMessage(
        { type: 'study_paused', message: 'paused', timestamp: 1000 },
        'study_abc',
        3,
      )
      expect(m.session_id).toBe('study:study_abc:round:3')
    })

    it('round param drives session_id (round 2 vs round 3)', () => {
      const r2 = buildEventMessage({ type: 'phase', message: 'x', timestamp: 1 }, 'study_abc', 2)
      const r3 = buildEventMessage({ type: 'phase', message: 'x', timestamp: 1 }, 'study_abc', 3)
      expect(r2.session_id).toBe('study:study_abc:round:2')
      expect(r3.session_id).toBe('study:study_abc:round:3')
    })
  })

  describe('buildAgentEventMessage (agent_* events)', () => {
    it('uses study:{id}:round:{N} session_id (matches loadMessages)', () => {
      const m = buildAgentEventMessage(
        { type: 'agent_text_delta', timestamp: 1000, agent: 'researcher', data: { text: 'hi' } },
        'study_abc',
        3,
      )
      expect(m.session_id).toBe('study:study_abc:round:3')
    })

    it('whitelist: low-level lifecycle events are skipped entirely', () => {
      // thinking/text-lifecycle/iter/usage events used to render as bare
      // event-name cards ("thinking_done") — must now return empty parts.
      for (const type of [
        'agent_thinking_start', 'agent_thinking_done', 'agent_thinking_end',
        'agent_text_started', 'agent_text.ended', 'agent_iter_start',
        'agent_iter_end', 'agent_loop_start', 'agent_loop_final',
        'agent_llm_usage', 'agent_session_total_tokens',
      ]) {
        const m = buildAgentEventMessage(
          { type, timestamp: 1000, agent: 'researcher', data: {} },
          'study_abc', 3,
        )
        expect(m.parts).toHaveLength(0)
      }
    })

    it('whitelist: assistant_message / tool_call / tool_result / text_delta render', () => {
      for (const type of [
        'agent_assistant_message', 'agent_tool_call',
        'agent_tool_result', 'agent_text_delta',
      ]) {
        const m = buildAgentEventMessage(
          { type, timestamp: 1000, agent: 'researcher', data: { text: 'x', content: 'x', tool: 'read' } },
          'study_abc', 3,
        )
        expect(m.parts.length).toBeGreaterThan(0)
      }
    })

    it('tool_result without tool name falls back to 工具 (not empty backticks)', () => {
      const m = buildAgentEventMessage(
        { type: 'agent_tool_result', timestamp: 1000, agent: 'researcher', data: { status: 'ok' } },
        'study_abc', 3,
      )
      expect(m.parts[0]).toMatchObject({ type: 'text' })
      const text = (m.parts[0] as { text: string }).text
      expect(text).toContain('工具')
      expect(text).not.toContain('`` ``')
    })

    it('skipped-text fallback still uses round session_id', () => {
      // No text path returns an empty-parts skip message; it must still
      // land in the same session or it disappears too.
      const m = buildAgentEventMessage(
        { type: 'agent_text_delta', timestamp: 1000, data: {} },
        'study_abc',
        2,
      )
      expect(m.session_id).toBe('study:study_abc:round:2')
    })
  })

  describe('regression guard', () => {
    it('never emits the legacy :stream session id', () => {
      const eventMsg = buildEventMessage(
        { type: 'study_round', message: 'round', timestamp: 1 },
        'study_abc',
        3,
      )
      const agentMsg = buildAgentEventMessage(
        { type: 'agent_text_delta', timestamp: 1, data: { text: 'x' } },
        'study_abc',
        3,
      )
      for (const m of [eventMsg, agentMsg]) {
        expect(m.session_id).not.toContain(':stream')
      }
    })

    it('source file never contains the legacy ":stream" session id', () => {
      // Catches future regressions where someone hard-codes the old id
      // again, breaking the MessageList filter alignment.
      expect(STUDY_CHAT_SRC).not.toMatch(/:stream/)
    })

    it('provider sessionId is the same expression as loadMessages', () => {
      // Both must build the same `study:{id}:round:{N}` string. We
      // verify by counting occurrences and checking they're well-formed.
      const roundSessionMatches = STUDY_CHAT_SRC.match(
        /study:\$\{studyId\}:round:\$\{(\w+)\}/g,
      ) ?? []
      // Three emitters (buildEventMessage, buildAgentEventMessage×2) +
      // loadMessages call site + providerSessionId = ≥4 occurrences.
      expect(roundSessionMatches.length).toBeGreaterThanOrEqual(4)
      // All variables used for the round number must be one of
      // {currentRound, selectedRound} so they stay aligned.
      const variables = roundSessionMatches.map((s) => {
        const m = s.match(/round:\$\{(\w+)\}/)
        return m?.[1]
      })
      for (const v of new Set(variables)) {
        expect(['currentRound', 'selectedRound']).toContain(v)
      }
    })
  })
})