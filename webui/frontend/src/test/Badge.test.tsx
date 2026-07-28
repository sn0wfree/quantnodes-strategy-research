import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge } from '../components/common/Badge'

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>Test Badge</Badge>)
    expect(screen.getByText('Test Badge')).toBeInTheDocument()
  })

  it('applies variant classes', () => {
    const { container } = render(<Badge variant="success">Success</Badge>)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toMatch(/emerald/)
  })

  it('applies size variants', () => {
    const { container } = render(<Badge size="md">Medium</Badge>)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toMatch(/py-1/)
  })
})