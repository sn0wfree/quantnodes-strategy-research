// todo store — replaceTodos / setExpanded / toggleExpanded / clearTodos,
// with per-session first-seen auto-expand via sessionStorage.

import { describe, it, expect, beforeEach } from 'vitest'
import { useTodoStore } from '../stores/todo'

const TODOS = [
  { id: 't1', content: '加载数据', status: 'in_progress' as const },
  { id: 't2', content: '计算因子', status: 'pending' as const },
]

const SESSION = 's-store-test'

beforeEach(() => {
  useTodoStore.setState({ todos: [], expanded: false })
  sessionStorage.clear()
})

describe('useTodoStore', () => {
  it('starts empty and collapsed', () => {
    const s = useTodoStore.getState()
    expect(s.todos).toEqual([])
    expect(s.expanded).toBe(false)
  })

  it('replaceTodos replaces the list', () => {
    useTodoStore.getState().replaceTodos(SESSION, TODOS)
    expect(useTodoStore.getState().todos).toEqual(TODOS)
  })

  it('auto-expands on the FIRST replaceTodos for a session', () => {
    useTodoStore.getState().replaceTodos(SESSION, TODOS, { expand: true })
    expect(useTodoStore.getState().expanded).toBe(true)
    expect(useTodoStore.getState().hasSeenFor(SESSION)).toBe(true)
  })

  it('does NOT auto-expand on subsequent updates once seen', () => {
    // First event marks seen + expands
    useTodoStore.getState().replaceTodos(SESSION, TODOS, { expand: true })
    // User collapses
    useTodoStore.getState().setExpanded(false)
    // Second event: must respect user's collapse
    useTodoStore.getState().replaceTodos(SESSION, TODOS, { expand: true })
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('does NOT auto-expand for a different session that has never seen it', () => {
    useTodoStore.getState().replaceTodos(SESSION, TODOS, { expand: true })
    useTodoStore.getState().setExpanded(false)
    // brand new session id → auto-expand again
    useTodoStore.getState().replaceTodos('s-other', TODOS, { expand: true })
    expect(useTodoStore.getState().expanded).toBe(true)
  })

  it('replaceTodos without expand flag leaves expansion state untouched', () => {
    useTodoStore.getState().replaceTodos(SESSION, TODOS)
    expect(useTodoStore.getState().expanded).toBe(false)
  })

  it('setExpanded and toggleExpanded work', () => {
    useTodoStore.getState().setExpanded(true)
    expect(useTodoStore.getState().expanded).toBe(true)
    useTodoStore.getState().toggleExpanded()
    expect(useTodoStore.getState().expanded).toBe(false)
    useTodoStore.getState().toggleExpanded()
    expect(useTodoStore.getState().expanded).toBe(true)
  })

  it('clearTodos resets list and collapses', () => {
    useTodoStore.getState().replaceTodos(SESSION, TODOS, { expand: true })
    useTodoStore.getState().clearTodos()
    const s = useTodoStore.getState()
    expect(s.todos).toEqual([])
    expect(s.expanded).toBe(false)
  })

  it('markSeenFor persists to sessionStorage', () => {
    useTodoStore.getState().markSeenFor(SESSION)
    expect(sessionStorage.getItem(`strategy-research:todo-seen:${SESSION}`)).toBe('1')
    expect(useTodoStore.getState().hasSeenFor(SESSION)).toBe(true)
  })
})