// TodoDrawer — renders todo list, progress, close; hidden when closed.

import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TodoDrawer } from '../components/chat/TodoDrawer'
import { useTodoStore } from '../stores/todo'

const TODOS = [
  { id: 't1', content: '加载数据', status: 'in_progress' as const },
  { id: 't2', content: '计算因子', status: 'pending' as const },
  { id: 't3', content: '回测验证', status: 'completed' as const },
]

beforeEach(() => {
  useTodoStore.setState({ todos: [], drawerOpen: false })
})

describe('TodoDrawer', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<TodoDrawer />)
    expect(container.querySelector('[data-testid="todo-drawer"]')).toBeNull()
  })

  it('shows todos with status and count', () => {
    useTodoStore.setState({ todos: TODOS, drawerOpen: true })
    render(<TodoDrawer />)
    expect(screen.getByText('加载数据')).toBeTruthy()
    expect(screen.getByText('计算因子')).toBeTruthy()
    expect(screen.getByText('回测验证')).toBeTruthy()
    expect(screen.getByText('1/3')).toBeTruthy()
  })

  it('marks completed items as done (line-through + 完成 label)', () => {
    useTodoStore.setState({ todos: TODOS, drawerOpen: true })
    render(<TodoDrawer />)
    expect(screen.getAllByText('完成').length).toBeGreaterThan(0)
    expect(screen.getByText('进行中')).toBeTruthy()
    expect(screen.getByText('待办')).toBeTruthy()
  })

  it('close button hides the drawer', () => {
    useTodoStore.setState({ todos: TODOS, drawerOpen: true })
    const { container } = render(<TodoDrawer />)
    fireEvent.click(screen.getByTitle('关闭任务抽屉'))
    expect(container.querySelector('[data-testid="todo-drawer"]')).toBeNull()
    expect(useTodoStore.getState().drawerOpen).toBe(false)
  })

  it('shows empty state when no todos', () => {
    useTodoStore.setState({ todos: [], drawerOpen: true })
    render(<TodoDrawer />)
    expect(screen.getByText('暂无任务')).toBeTruthy()
  })
})
