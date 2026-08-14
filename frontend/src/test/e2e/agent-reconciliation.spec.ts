/**
 * agent-reconciliation.spec.ts — plan FE-07/FE-11 operator surface: the admin
 * reconciliation page (list/detail/evidence/CAS resolve).  Self-skips until
 * the reconciliation API is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'


test('FE-11 reconciliation: admin page renders the case list shell', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/admin/agent-reconciliations')), 'reconciliation API not registered yet')
  await loginAsAdmin(page)
  await page.goto('/admin/agent-reconciliations')
  await expect(page).toHaveURL(/\/admin\/agent-reconciliations$/)
  // list shell renders (loading, empty, or cases)
  await expect(
    page.locator('[data-testid="reconciliation-loading"], [data-testid="reconciliation-empty"], [data-testid="agent-reconciliation-page"]').first(),
  ).toBeVisible({ timeout: 15_000 })
})
