import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Deterministic, UTC-based tests
process.env.TZ = 'UTC'

// Polyfills only when absent: jsdom 27 / Node 26 already provide
// TextEncoder/TextDecoder and Web Streams, so nothing needs shimming here.
// Feature packets configure their MSW servers with
// `server.listen({ onUnhandledRequest: 'error' })`.

afterEach(() => {
  cleanup()
})
