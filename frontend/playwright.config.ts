import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './src/test/e2e',
  fullyParallel: false,
  retries: 1,
  // business-journeys.spec.ts drives real Pipeline/Curated/DeepSeek-backed
  // Runtime turns, well past this suite's previous 30s budget; the spec
  // itself pins `trace: 'on'`/`screenshot: 'on'` (test.use), so this bump
  // only affects overall per-test/assertion timeouts.
  // The browser creates an Agent and Session before the 930-second real-model
  // answer wait begins, so the total test budget must include both phases.
  timeout: 990_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60000,
  },
})
