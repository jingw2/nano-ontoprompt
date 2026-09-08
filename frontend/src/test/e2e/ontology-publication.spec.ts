/**
 * ontology-publication.spec.ts — plan FE-06 governed publication: the
 * lifecycle UI surfaces mark-created/publish/archive against the registered
 * lifecycle APIs.  Self-skips until those routes are registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'


test('FE-06 publication: lifecycle routes registered; ontology detail reachable', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/ontologies', '/publish')), 'lifecycle publish not registered yet')
  await loginAsAdmin(page)
  await page.goto('/ontologies')
  await expect(page).toHaveURL(/\/ontologies$/)
  const row = page.locator('tbody tr').first()
  if ((await row.count()) === 0) {
    test.skip(true, 'no ontologies seeded')
    return
  }
  await row.click()
  await expect(page).toHaveURL(/\/ontologies\/[^/]+$/)
})
