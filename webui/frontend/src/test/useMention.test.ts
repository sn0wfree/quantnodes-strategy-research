// useMention — @-mention picker state machine:
//   - checkMention detects "@…" tokens and activates the popover
//   - filtered is case-insensitive and follows query
//   - handleKeyDown handles ArrowUp/ArrowDown/Enter/Escape
//   - selectItem splices the chosen item into the text

import { describe, it, expect } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useMention, type MentionItem } from '../hooks/useMention'

const items: MentionItem[] = [
  { id: 'a', name: 'analyst', type: 'agent' },
  { id: 'r', name: 'researcher', type: 'agent' },
  { id: 'f', name: 'README', type: 'file' },
]

describe('useMention', () => {
  it('filters items case-insensitively', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('see @READ', 9))
    expect(result.current.filtered.map((i) => i.name)).toEqual(['README'])
  })

  it('returns false when there is no @ token', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('no mention here', 5))
    expect(result.current.active).toBe(false)
  })

  it('deactivates on a non-matching tail', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('hi @analyst', 12))
    expect(result.current.active).toBe(true)
    act(() => result.current.checkMention('hi @analyst no tail', 12))
    expect(result.current.active).toBe(false)
  })

  it('navigates with ArrowDown and wraps around', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('@', 1))
    expect(result.current.selectedIndex).toBe(0)
    act(() =>
      result.current.handleKeyDown(
        { key: 'ArrowDown', preventDefault: () => {} } as never,
        '@',
        () => {}
      )
    )
    expect(result.current.selectedIndex).toBe(1)
    act(() =>
      result.current.handleKeyDown(
        { key: 'ArrowDown', preventDefault: () => {} } as never,
        '@',
        () => {}
      )
    )
    expect(result.current.selectedIndex).toBe(2)
    // wraps
    act(() =>
      result.current.handleKeyDown(
        { key: 'ArrowDown', preventDefault: () => {} } as never,
        '@',
        () => {}
      )
    )
    expect(result.current.selectedIndex).toBe(0)
  })

  it('ArrowUp wraps in reverse', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('@', 1))
    act(() =>
      result.current.handleKeyDown(
        { key: 'ArrowUp', preventDefault: () => {} } as never,
        '@',
        () => {}
      )
    )
    expect(result.current.selectedIndex).toBe(items.length - 1)
  })

  it('Enter picks the highlighted item and reports handled=true', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('hello @', 7))
    let pickedName = ''
    const handled = result.current.handleKeyDown(
      { key: 'Enter', preventDefault: () => {} } as never,
      'hello @',
      (i) => {
        pickedName = i.name
      }
    )
    expect(handled).toBe(true)
    expect(pickedName).toBe('analyst')
  })

  it('Escape deactivates without selecting', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('@', 1))
    act(() =>
      result.current.handleKeyDown(
        { key: 'Escape', preventDefault: () => {} } as never,
        '@',
        () => {}
      )
    )
    expect(result.current.active).toBe(false)
  })

  it('selectItem splices the chosen item into the surrounding text', () => {
    const { result } = renderHook(() => useMention(items))
    act(() => result.current.checkMention('hi @an there', 6))
    const picked = items[0]
    // Source: before (incl. "@") + "@" + item.name + " " + after
    const out = result.current.selectItem('hi @an there', picked)
    expect(out.startsWith('hi @analyst')).toBe(true)
    expect(out.endsWith('there')).toBe(true)
  })

  it('returns false from handleKeyDown when inactive', () => {
    const { result } = renderHook(() => useMention(items))
    const handled = result.current.handleKeyDown(
      { key: 'ArrowDown', preventDefault: () => {} } as never,
      '',
      () => {}
    )
    expect(handled).toBe(false)
  })
})