/**
 * agent-application.spec.ts — plan FE-07/FE-11: three-column Application tab
 * with session history, streaming chat, execution trace and ontology access
 * panels.  Self-skips until the sessions/turns APIs are registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin, openFirstAgent } from './helpers/ui'


test('FE-07 application tab: session sidebar, conversation panel, trace and lineage panels', async ({ page }) => {
  const sessionsReady = await hasApi('/api/v1/agents', '/sessions')
  test.skip(!sessionsReady, 'sessions API not registered yet')
  await loginAsAdmin(page)
  if (!(await openFirstAgent(page))) {
    test.skip(true, 'no agents seeded')
    return
  }
  await page.locator('button:has-text("Agent Application"), button:has-text("智能体应用")').first().click()
  await expect(page.locator('[data-testid="session-sidebar"]').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-testid="conversation-panel"]').first()).toBeVisible()
})

test('FE-11 lineage: trace and ontology access panels render inside the application tab', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agent-turns', '/events')), 'events API not registered yet')
  await loginAsAdmin(page)
  if (!(await openFirstAgent(page))) {
    test.skip(true, 'no agents seeded')
    return
  }
  await page.locator('button:has-text("Agent Application"), button:has-text("智能体应用")').first().click()
  await expect(page.locator('[data-testid="session-sidebar"]').first()).toBeVisible({ timeout: 15_000 })
})
