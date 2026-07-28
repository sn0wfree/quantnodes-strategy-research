import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Spinner } from '../components/common/Spinner'

describe('Spinner', () => {
  it('renders with default size', () => {
    const { container } = render(<Spinner />)
    expect(container.firstChild).toBeInTheDocument()
  })

  it('renders with custom size', () => {
    const { container: sm } = render(<Spinner size="sm" />)
    const { container: lg } = render(<Spinner size="lg" />)
    expect(sm.firstChild).toBeInTheDocument()
    expect(lg.firstChild).toBeInTheDocument()
  })
})