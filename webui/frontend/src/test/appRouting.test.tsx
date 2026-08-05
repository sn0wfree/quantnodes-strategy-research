// App routing tests — /register route + login page register link.

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from '../App'

vi.mock('../api/client', async () => {
  return {
    api: {
      post: vi.fn(),
    },
    ApiError: class extends Error {},
  }
})

import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'

describe('App routing', () => {
  it('renders the register page at /register', () => {
    render(
      <MemoryRouter initialEntries={['/register']}>
        <App />
      </MemoryRouter>
    )
    expect(screen.getByText('创建账号')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /注册/ })).toBeInTheDocument()
  })

  it('login page links to /register', () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <App />
      </MemoryRouter>
    )
    expect(screen.getByText('没有账号？')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '注册' })).toBeInTheDocument()
  })

  it('register flow submits and navigates to /', async () => {
    vi.mocked(api.post).mockResolvedValue({
      access_token: 'tok',
      user: { id: 'u1', username: 'newuser' },
    } as never)
    render(
      <MemoryRouter initialEntries={['/register']}>
        <App />
      </MemoryRouter>
    )
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newuser' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: /注册/ }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/auth/register', {
        username: 'newuser',
        display_name: 'newuser',
        password: 'secret',
      })
    )
    expect(useAuthStore.getState().token).toBe('tok')
  })
})
