import { defineConfig, devices } from '@playwright/test'

/**
 * I-FRONTEND agent E2E suite (frontend/src/test/e2e).
 *
 * Functional specs map to plan FE-01..FE-11 and self-skip until their backend
 * surface is registered (see helpers/availability pattern in the specs).
 * `scripts/run_agent_e2e.sh` starts the pinned API/worker/frontend stack and
 * protects test-results/.last-run.json; the Vite dev server here reuses an
 * already-running instance (reuseExistingServer).
 */
export default defineConfig({
  testDir: './',
  testMatch: [
    'agent-navigation.spec.ts',
    'agent-list.spec.ts',
    'agent-configuration.spec.ts',
    'agent-application.spec.ts',
    'agent-approval.spec.ts',
    'agent-reconciliation.spec.ts',
    'ontology-publication.spec.ts',
  ],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
  reporter: [['list']],
})
