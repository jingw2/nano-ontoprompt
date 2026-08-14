import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Deterministic, UTC-based tests
process.env.TZ = 'UTC'

// jsdom 27 under Node 26 exposes empty `localStorage` getters (both on
// globalThis and window) that yield undefined, which breaks modules that read
// storage at import time (i18n).  Install a deterministic in-memory Storage.
const storageMap = new Map<string, string>()
const memoryStorage: Storage = {
  getItem: (k) => storageMap.get(k) ?? null,
  setItem: (k, v) => { storageMap.set(k, String(v)) },
  removeItem: (k) => { storageMap.delete(k) },
  clear: () => { storageMap.clear() },
  key: (i) => [...storageMap.keys()][i] ?? null,
  get length() { return storageMap.size },
}
Object.defineProperty(globalThis, 'localStorage', {
  value: memoryStorage,
  configurable: true,
  writable: true,
})

// Polyfills only when absent: jsdom 27 / Node 26 already provide
// TextEncoder/TextDecoder and Web Streams, so nothing needs shimming here.
// Feature packets configure their MSW servers with
// `server.listen({ onUnhandledRequest: 'error' })`.

afterEach(() => {
  cleanup()
})
