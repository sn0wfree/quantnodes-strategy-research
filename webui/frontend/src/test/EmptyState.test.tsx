import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { EmptyState } from '../components/common/EmptyState'
import { Sparkles } from 'lucide-react'

describe('EmptyState', () => {
  it('renders title', () => {
    const { container } = render(<EmptyState title="No sessions yet" />)
    expect(container.textContent).toContain('No sessions yet')
  })

  it('renders description when provided', () => {
    const { container } = render(
      <EmptyState title="Empty" description="Start by creating your first session" />
    )
    expect(container.textContent).toContain('Start by creating your first session')
  })

  it('renders custom icon when provided', () => {
    const { container } = render(
      <EmptyState title="Empty" icon={<Sparkles data-testid="custom-icon" />} />
    )
    expect(container.querySelector('[data-testid="custom-icon"]')).toBeTruthy()
  })

  it('renders action when provided', () => {
    const { container } = render(
      <EmptyState
        title="Empty"
        action={<button data-testid="action-btn">Create</button>}
      />
    )
    expect(container.querySelector('[data-testid="action-btn"]')).toBeTruthy()
  })

  it('omits description when not provided', () => {
    const { container } = render(<EmptyState title="Just title" />)
    // Description <p> should not exist
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs.length).toBe(0)
  })
})