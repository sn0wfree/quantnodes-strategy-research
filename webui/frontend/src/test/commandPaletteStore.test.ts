import { describe, it, expect, beforeEach } from 'vitest'
import { useCommandPaletteStore } from '../stores/commandPalette'

describe('useCommandPaletteStore', () => {
  beforeEach(() => {
    useCommandPaletteStore.setState({ open: false })
  })

  it('starts closed', () => {
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })

  it('toggle flips state', () => {
    useCommandPaletteStore.getState().toggle()
    expect(useCommandPaletteStore.getState().open).toBe(true)

    useCommandPaletteStore.getState().toggle()
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })

  it('setOpen sets explicit value', () => {
    useCommandPaletteStore.getState().setOpen(true)
    expect(useCommandPaletteStore.getState().open).toBe(true)

    useCommandPaletteStore.getState().setOpen(false)
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })

  it('setOpen overrides current state', () => {
    useCommandPaletteStore.setState({ open: true })
    useCommandPaletteStore.getState().setOpen(false)
    expect(useCommandPaletteStore.getState().open).toBe(false)
  })
})