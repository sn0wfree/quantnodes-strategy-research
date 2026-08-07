import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { SplitDivider } from '../components/layout/SplitDivider'

function mockParentWidth(width: number) {
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    value: width,
  })
}

describe('SplitDivider', () => {
  it('renders a single hit-area wrapper with a centered 1px visual line', () => {
    const { container } = render(<SplitDivider onDrag={() => {}} />)
    const hit = screen.getByTestId('split-divider')
    expect(hit).toBe(container.firstChild)
    // The 1px visual line is the only child of the hit-area.
    expect(hit.children).toHaveLength(1)
    const visual = hit.firstChild as HTMLElement
    expect(visual.className).toMatch(/w-px/)
  })

  it('reports a positive delta when the pointer moves right', () => {
    mockParentWidth(1000)
    const onDrag = vi.fn()
    const { container } = render(<SplitDivider onDrag={onDrag} />)
    const divider = container.firstChild as HTMLElement
    // getBoundingClientRect of the parent defaults to 0 in jsdom, so stub it.
    const parent = divider.parentElement as HTMLElement
    vi.spyOn(parent, 'getBoundingClientRect').mockReturnValue({
      width: 1000, height: 0, top: 0, left: 0, right: 1000, bottom: 0,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect)

    fireEvent.mouseDown(divider, { clientX: 100 })
    fireEvent.mouseMove(window, { clientX: 150 })
    fireEvent.mouseUp(window)

    expect(onDrag).toHaveBeenCalledWith(0.05)
  })

  it('reports a negative delta when the pointer moves left', () => {
    mockParentWidth(1000)
    const onDrag = vi.fn()
    const { container } = render(<SplitDivider onDrag={onDrag} />)
    const divider = container.firstChild as HTMLElement
    const parent = divider.parentElement as HTMLElement
    vi.spyOn(parent, 'getBoundingClientRect').mockReturnValue({
      width: 1000, height: 0, top: 0, left: 0, right: 1000, bottom: 0,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect)

    fireEvent.mouseDown(divider, { clientX: 100 })
    fireEvent.mouseMove(window, { clientX: 75 })
    fireEvent.mouseUp(window)

    expect(onDrag).toHaveBeenCalledWith(-0.025)
  })

  it('does not report drags before mousedown', () => {
    mockParentWidth(1000)
    const onDrag = vi.fn()
    const { container } = render(<SplitDivider onDrag={onDrag} />)
    const divider = container.firstChild as HTMLElement
    const parent = divider.parentElement as HTMLElement
    vi.spyOn(parent, 'getBoundingClientRect').mockReturnValue({
      width: 1000, height: 0, top: 0, left: 0, right: 1000, bottom: 0,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect)

    fireEvent.mouseMove(window, { clientX: 150 })
    expect(onDrag).not.toHaveBeenCalled()
  })
})