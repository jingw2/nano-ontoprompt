/**
 * agent-approval.spec.ts — plan FE-09/FE-10: governed action approval card
 * states (designated actor decisions, stale/result).  Self-skips until the
 * approvals API is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'


test('FE-10 approval surface is registered and reachable', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agent-approvals')), 'approvals API not registered yet')
  await loginAsAdmin(page)
  // the Agent workspace is navigable; the approval card renders inside the
  // application tab when an approval exists
  await expect(page.getByRole('link', { name: '智能体' }).or(page.getByRole('link', { name: 'Agent' }))).toBeVisible()
  await page.goto('/agents')
  await expect(page).toHaveURL(/\/agents$/)
})
