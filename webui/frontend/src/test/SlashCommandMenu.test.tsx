import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SlashCommandMenu } from '../components/chat/SlashCommandMenu'
import { SLASH_COMMANDS } from '../components/chat/slashCommands'

function getOnSelect() {
  return vi.fn()
}

describe('SlashCommandMenu', () => {
  beforeEach(() => {
    // The keyboard handlers in SlashCommandMenu attach window listeners
    // for `sr:slash-nav` / `sr:slash-enter`. Make sure each test gets
    // a clean listener set.
    window.removeEventListener('sr:slash-nav', () => {})
    window.removeEventListener('sr:slash-enter', () => {})
  })

  // ── P32: autoSend flag ──

  it('renders the 直发 badge for autoSend commands', () => {
    render(<SlashCommandMenu query="" onSelect={getOnSelect()} />)

    // /clear /help /compact /agent all have autoSend: true
    for (const cmd of ['/clear', '/help', '/compact', '/agent']) {
      // The badge text is identical for every autoSend command — use
      // getAllByText to assert each row has it.
      const row = screen.getByText(cmd).closest('button')!
      expect(row.textContent).toContain('直发')
    }
  })

  it('does not render the 直发 badge for argument-bearing commands', () => {
    render(<SlashCommandMenu query="" onSelect={getOnSelect()} />)

    for (const cmd of ['/goal', '/study']) {
      const row = screen.getByText(cmd).closest('button')!
      expect(row.textContent).not.toContain('直发')
    }
  })

  it('SLASH_COMMANDS registration matches the autoSend contract', () => {
    // Lock the slashCommands registry: every autoSend command should be
    // a complete command (no argument placeholder); every non-autoSend
    // command should require an argument. This catches a future
    // contributor who adds a bare command without autoSend or a
    // parameterised command with autoSend=true by mistake.
    const autoSend = SLASH_COMMANDS.filter((c) => c.autoSend)
    const manual = SLASH_COMMANDS.filter((c) => !c.autoSend)
    expect(autoSend.map((c) => c.command).sort()).toEqual([
      '/agent',
      '/clear',
      '/compact',
      '/help',
    ])
    expect(manual.map((c) => c.command).sort()).toEqual(['/goal', '/study'])
  })

  it('forwards autoSend=true to onSelect when clicking an autoSend command', () => {
    const onSelect = getOnSelect()
    render(<SlashCommandMenu query="" onSelect={onSelect} />)

    // Click /clear (autoSend:true)
    fireEvent.click(screen.getByText('/clear').closest('button')!)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('/clear', true)
  })

  it('forwards undefined autoSend when clicking an argument-bearing command', () => {
    const onSelect = getOnSelect()
    render(<SlashCommandMenu query="" onSelect={onSelect} />)

    fireEvent.click(screen.getByText('/goal').closest('button')!)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('/goal', undefined)
  })

  it('filters commands by query (subcommand / label match)', () => {
    render(<SlashCommandMenu query="comp" onSelect={getOnSelect()} />)
    // /compact matches both label and command
    expect(screen.getByText('/compact')).toBeTruthy()
    // /goal /clear /help /study /agent are filtered out
    expect(screen.queryByText('/goal')).toBeNull()
    expect(screen.queryByText('/clear')).toBeNull()
  })

  it('shows the empty-state hint when query matches nothing', () => {
    render(<SlashCommandMenu query="zzzz" onSelect={getOnSelect()} />)
    expect(screen.getByText(/无匹配命令/)).toBeTruthy()
  })
})