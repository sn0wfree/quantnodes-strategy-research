// Skeleton unit tests — variant classes + custom className pass-through.

import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Skeleton, MessageSkeleton } from '../components/common/Skeleton'

describe('Skeleton', () => {
  it('applies the rect variant by default', () => {
    const { container } = render(<Skeleton />)
    const el = container.querySelector('.animate-pulse')!
    expect(el.className).toMatch(/rounded-lg/)
  })

  it('renders the circle variant', () => {
    const { container } = render(<Skeleton variant="circle" />)
    const el = container.querySelector('.animate-pulse')!
    expect(el.className).toMatch(/rounded-full/)
  })

  it('renders the text variant', () => {
    const { container } = render(<Skeleton variant="text" />)
    const el = container.querySelector('.animate-pulse')!
    expect(el.className).toMatch(/\bh-4\b/)
  })

  it('passes through extra className', () => {
    const { container } = render(<Skeleton className="w-24" />)
    const el = container.querySelector('.animate-pulse')!
    expect(el.className).toMatch(/w-24/)
  })

  it('MessageSkeleton renders multiple sub-skeletons', () => {
    const { container } = render(<MessageSkeleton />)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThanOrEqual(3)
  })
})