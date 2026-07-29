import '@testing-library/jest-dom'
import { enableMapSet } from 'immer'
import { vi } from 'vitest'
import { createElement } from 'react'

// Enable Map/Set support for Immer
enableMapSet()

// Mock the heavy syntax highlighter globally (refractor is ESM-only, can't load in CJS test env)
// Used by MarkdownRenderer via react-syntax-highlighter.
vi.mock('react-syntax-highlighter', () => ({
  Prism: ({ children }: any) =>
    createElement('pre', { 'data-testid': 'syntax-highlighter' }, children),
}))
vi.mock('react-syntax-highlighter/dist/esm/styles/prism', () => ({
  oneDark: {},
}))

// Mock crypto.randomUUID if not available
if (!global.crypto) {
  global.crypto = {} as any
}
if (!global.crypto.randomUUID) {
  let counter = 0
  global.crypto.randomUUID = () =>
    `test-uuid-0000-0000-0000-${String(++counter).padStart(12, '0')}` as `${string}-${string}-${string}-${string}-${string}`
}

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})