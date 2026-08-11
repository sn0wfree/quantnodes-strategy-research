import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useDagStepApply } from '../hooks/useDagStepApply'
import { useChatStore } from '../stores/chat'
import type { Message, ToolCallPart } from '../stores/chat'

function makeMessage(sessionId: string, parts: Message['parts']): Message {
  return {
    id: `m-${Math.random().toString(36).slice(2, 8)}`,
    session_id: sessionId,
    role: 'assistant',
    parts,
    created_at: Date.now() / 1000,
  }
}

function makeToolCall(name: string, args: unknown, result?: unknown, status: 'running' | 'done' | 'error' = 'done'): ToolCallPart {
  return {
    type: 'tool_call',
    id: `call_${Math.random().toString(36).slice(2, 8)}`,
    name,
    arguments: JSON.stringify(args),
    result: result === undefined ? undefined : JSON.stringify(result),
    status,
  }
}

beforeEach(() => {
  useChatStore.setState({ messages: new Map() })
})

afterEach(() => {
  useChatStore.setState({ messages: new Map() })
})

describe('useDagStepApply', () => {
  it('applies submit_dag_step tool completions in dag: sessions', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const dag = {
      nodes: [{ id: 'a', type: 'llm_agent', label: 'A', config: { role: 'researcher' } }],
      edges: [],
    }
    const tc = makeToolCall('submit_dag_step', { dag }, { applied: true, nodes: 1, edges: 0 })
    const msg = makeMessage('dag:alpha_research', [tc])
    useChatStore.getState().addMessage(msg)

    // subscribe fires synchronously
    await Promise.resolve()

    expect(onApplyDag).toHaveBeenCalledTimes(1)
    const spec = onApplyDag.mock.calls[0][0]
    expect(spec.nodes).toHaveLength(1)
    expect(spec.nodes[0].id).toBe('a')
    expect(spec.nodes[0].type).toBe('llm_agent')
    expect(spec.nodes[0].config.role).toBe('researcher')
  })

  it('ignores submit_dag_step in non-dag sessions', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc = makeToolCall('submit_dag_step', { dag: { nodes: [], edges: [] } }, { applied: true })
    const msg = makeMessage('sess-other', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).not.toHaveBeenCalled()
  })

  it('ignores submit_dag_step with applied=false', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc = makeToolCall(
      'submit_dag_step',
      { dag: { nodes: [], edges: [] } },
      { applied: false, errors: ['bad id'] },
    )
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).not.toHaveBeenCalled()
  })

  it('ignores submit_dag_step while status=running', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc = makeToolCall(
      'submit_dag_step',
      { dag: { nodes: [], edges: [] } },
      { applied: true },
      'running',
    )
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).not.toHaveBeenCalled()
  })

  it('ignores other tool names', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc = makeToolCall('read_file', { path: 'x.md' }, { status: 'ok' })
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).not.toHaveBeenCalled()
  })

  it('is idempotent: a second event for the same tool_call id does not re-apply', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc = makeToolCall('submit_dag_step', { dag: { nodes: [], edges: [] } }, { applied: true })
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)
    await Promise.resolve()
    expect(onApplyDag).toHaveBeenCalledTimes(1)

    // Re-emit the same store state — the Set must block the second apply.
    useChatStore.getState().addMessage({ ...msg, id: msg.id + '-v2' })
    await Promise.resolve()
    expect(onApplyDag).toHaveBeenCalledTimes(1)
  })

  it('skips malformed result JSON without throwing', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc: ToolCallPart = {
      type: 'tool_call',
      id: 'call_bad',
      name: 'submit_dag_step',
      arguments: JSON.stringify({ dag: { nodes: [], edges: [] } }),
      result: 'not-json-at-all',
      status: 'done',
    }
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).not.toHaveBeenCalled()
  })

  it('does not crash when arguments is not a string', async () => {
    const onApplyDag = vi.fn()
    renderHook(() => useDagStepApply(onApplyDag))

    const tc: ToolCallPart = {
      type: 'tool_call',
      id: 'call_obj_args',
      name: 'submit_dag_step',
      arguments: { dag: { nodes: [{ id: 'a', type: 'llm_agent', label: 'A', config: { role: 'r' } }], edges: [] } } as unknown as string,
      result: JSON.stringify({ applied: true }),
      status: 'done',
    }
    const msg = makeMessage('dag:foo', [tc])
    useChatStore.getState().addMessage(msg)

    await Promise.resolve()
    expect(onApplyDag).toHaveBeenCalledTimes(1)
  })
})