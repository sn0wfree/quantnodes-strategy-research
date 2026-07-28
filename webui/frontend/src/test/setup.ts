import '@testing-library/jest-dom'
import { enableMapSet } from 'immer'

// Enable Map/Set support for Immer
enableMapSet()

// Mock crypto.randomUUID if not available
if (!global.crypto) {
  global.crypto = {} as any
}
if (!global.crypto.randomUUID) {
  let counter = 0
  global.crypto.randomUUID = () => `test-uuid-${++counter}`
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