/**
 * agent-list.spec.ts — plan FE-01 list behavior: server-filtered agent list
 * with the create affordance and ID filter.  Self-skips until the agents API
 * is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'


test('FE-01 list: create affordance navigates to the wizard', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agents')), 'backend /api/v1/agents not registered yet')
  await loginAsAdmin(page)
  await page.goto('/agents')
  await expect(page).toHaveURL(/\/agents$/)
  const create = page.locator('button:has-text("新建 Agent"), button:has-text("Create Agent")').first()
  await expect(create).toBeVisible()
  await create.click()
  await expect(page).toHaveURL(/\/agents\/new$/)
})

test('FE-01 list: ID filter input is present', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agents')), 'backend /api/v1/agents not registered yet')
  await loginAsAdmin(page)
  await page.goto('/agents')
  await expect(
    page.locator('input[placeholder*="ID"], input[placeholder*="ID"]').first(),
  ).toBeVisible()
})
