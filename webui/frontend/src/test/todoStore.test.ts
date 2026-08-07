// todo store — replaceTodos / setDrawerOpen / clearTodos.

import { describe, it, expect, beforeEach } from 'vitest'
import { useTodoStore } from '../stores/todo'

const TODOS = [
  { id: 't1', content: '加载数据', status: 'in_progress' as const },
  { id: 't2', content: '计算因子', status: 'pending' as const },
]

beforeEach(() => {
  useTodoStore.setState({ todos: [], drawerOpen: false })
})

describe('useTodoStore', () => {
  it('replaceTodos replaces the list and keeps drawer state by default', () => {
    useTodoStore.getState().replaceTodos(TODOS)
    expect(useTodoStore.getState().todos).toEqual(TODOS)
    expect(useTodoStore.getState().drawerOpen).toBe(false)
  })

  it('replaceTodos can auto-open the drawer', () => {
    useTodoStore.getState().replaceTodos(TODOS, { open: true })
    expect(useTodoStore.getState().drawerOpen).toBe(true)
  })

  it('setDrawerOpen toggles visibility', () => {
    useTodoStore.getState().setDrawerOpen(true)
    expect(useTodoStore.getState().drawerOpen).toBe(true)
    useTodoStore.getState().setDrawerOpen(false)
    expect(useTodoStore.getState().drawerOpen).toBe(false)
  })

  it('clearTodos resets list and closes the drawer', () => {
    useTodoStore.getState().replaceTodos(TODOS, { open: true })
    useTodoStore.getState().clearTodos()
    expect(useTodoStore.getState().todos).toEqual([])
    expect(useTodoStore.getState().drawerOpen).toBe(false)
  })
})
