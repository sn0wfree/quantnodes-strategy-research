// todo_updated SSE handler — replaces the todo list + auto-opens drawer.

import { describe, it, expect, beforeEach } from 'vitest'
import { useTodoStore } from '../stores/todo'
import { todoUpdated } from '../hooks/sse/todoHandlers'
import { useChatStore } from '../stores/chat'

function ctx() {
  return {
    sessionId: 's-1',
    messageId: 'm-1',
    updateMessage: () => {},
    state: useChatStore.getState(),
  } as never
}

beforeEach(() => {
  useTodoStore.setState({ todos: [], drawerOpen: false })
})

describe('todoUpdated handler', () => {
  it('replaces todos and opens the drawer', () => {
    todoUpdated(
      {
        todos: [
          { id: 't1', content: '加载数据', status: 'in_progress' },
          { id: 't2', content: '计算因子', status: 'pending' },
        ],
      },
      ctx(),
    )
    const s = useTodoStore.getState()
    expect(s.todos).toHaveLength(2)
    expect(s.todos[0].content).toBe('加载数据')
    expect(s.drawerOpen).toBe(true)
  })

  it('ignores malformed payloads', () => {
    todoUpdated({}, ctx())
    todoUpdated({ todos: 'not-an-array' }, ctx())
    todoUpdated({ todos: [{ id: 1, status: 'x' }] }, ctx())
    expect(useTodoStore.getState().todos).toEqual([])
  })

  it('filters items without valid id/content', () => {
    todoUpdated(
      {
        todos: [
          { id: 't1', content: 'ok', status: 'pending' },
          { id: 't2', status: 'pending' },
          { content: 'no-id', status: 'pending' },
        ],
      },
      ctx(),
    )
    const s = useTodoStore.getState()
    expect(s.todos).toHaveLength(1)
    expect(s.todos[0].id).toBe('t1')
    expect(s.drawerOpen).toBe(true)
  })
})
