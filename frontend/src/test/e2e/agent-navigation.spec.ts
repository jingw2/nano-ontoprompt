/**
 * agent-navigation.spec.ts — plan FE-01 (navigation/list) + the I-FRONTEND
 * red contract.  Self-skips functional scenarios until /api/v1/agents is
 * registered in the live backend OpenAPI.
 */
import { test, expect } from '@playwright/test'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const FRONTEND = resolve(HERE, '..', '..', '..')

test('I-FRONTEND red contract', () => {
  const failures: string[] = []
  const read = (p: string) => readFileSync(resolve(FRONTEND, p), 'utf8')

  const app = read('src/App.tsx')
  for (const route of ['/agents', '/agents/new', '/agents/:id', '/admin/agent-reconciliations']) {
    if (!app.includes(`path="${route}"`)) failures.push(`App.tsx missing route ${route}`)
  }
  if (!app.includes('AgentReconciliationPage')) failures.push('App.tsx missing admin reconciliation page wiring')

  const layout = read('src/components/Layout.tsx')
  if (!layout.includes("nav.agents")) failures.push('Layout.tsx missing Agent sidebar entry')
  if (!layout.includes('agent-reconciliations')) failures.push('Layout.tsx missing admin reconciliation entry')

  for (const locale of ['en', 'zh']) {
    const i18n = read(`src/i18n/${locale}.json`)
    if (!i18n.includes('"agents"')) failures.push(`i18n/${locale}.json missing nav.agents`)
  }

  for (const p of ['scripts/run_agent_e2e.sh', 'frontend/src/test/e2e/fixtures/agentMvp.ts']) {
    if (!existsSync(resolve(FRONTEND, '..', p))) failures.push(`missing ${p}`)
  }
  if (failures.length) throw new Error('RED_I_FRONTEND: ' + failures.join('; '))
})

test('agents route, bilingual sidebar entry, server-filtered list shell', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agents')), 'backend /api/v1/agents not registered yet')

  await loginAsAdmin(page)
  // bilingual sidebar entry (Chinese UI is the default locale)
  await expect(page.locator('text=智能体').first().or(page.locator('text=Agent').first())).toBeVisible()
  await page.goto('/agents')
  await expect(page).toHaveURL(/\/agents$/)
  await expect(
    page.locator('button:has-text("新建 Agent")').first()
      .or(page.locator('button:has-text("Create Agent")').first()),
  ).toBeVisible()
  await expect(page.locator('th', { hasText: 'ID' }).first()).toBeVisible()
})
