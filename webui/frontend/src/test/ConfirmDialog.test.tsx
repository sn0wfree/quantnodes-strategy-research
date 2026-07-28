import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { ConfirmDialog } from '../components/common/ConfirmDialog'

describe('ConfirmDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    title: 'Delete session?',
    description: 'This action cannot be undone.',
    onConfirm: vi.fn(),
  }

  it('renders title and description when open', () => {
    render(<ConfirmDialog {...defaultProps} />)
    expect(screen.getByText('Delete session?')).toBeTruthy()
    expect(screen.getByText('This action cannot be undone.')).toBeTruthy()
  })

  it('calls onConfirm when confirm button clicked', () => {
    const onConfirm = vi.fn()
    render(
      <ConfirmDialog {...defaultProps} onConfirm={onConfirm} confirmLabel="Delete" />
    )

    fireEvent.click(screen.getByText('Delete'))

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onOpenChange(false) when cancel button clicked', () => {
    const onOpenChange = vi.fn()
    render(
      <ConfirmDialog {...defaultProps} onOpenChange={onOpenChange} cancelLabel="取消" />
    )

    // There are two buttons with text "取消" (one in content, one as close X overlay)
    const buttons = screen.getAllByText('取消')
    fireEvent.click(buttons[0])

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('uses default Chinese labels', () => {
    render(<ConfirmDialog {...defaultProps} />)
    expect(screen.getAllByText('确认').length).toBeGreaterThan(0)
    expect(screen.getAllByText('取消').length).toBeGreaterThan(0)
  })

  it('applies danger styling when variant=danger', () => {
    render(<ConfirmDialog {...defaultProps} variant="danger" />)
    const confirmBtn = screen.getAllByText('确认').find(
      (el) => el.tagName === 'BUTTON'
    ) as HTMLButtonElement
    expect(confirmBtn.className).toContain('bg-red-600')
  })

  it('applies default (primary) styling when variant=default', () => {
    render(<ConfirmDialog {...defaultProps} />)
    const confirmBtn = screen.getAllByText('确认').find(
      (el) => el.tagName === 'BUTTON'
    ) as HTMLButtonElement
    expect(confirmBtn.className).toContain('bg-primary-600')
    expect(confirmBtn.className).not.toContain('bg-red-600')
  })

  it('does not render content when closed', () => {
    render(<ConfirmDialog {...defaultProps} open={false} />)
    expect(screen.queryByText('Delete session?')).toBeNull()
  })
})