import { expect, test } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { hasLocalRuntimeGovernanceSeed, readRefreshSeed, runtimeSeedId } from './helpers/runtime'
import { loginAsAdmin } from './helpers/ui'

test.describe('runtime governance operator surfaces', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!hasLocalRuntimeGovernanceSeed(),
      'requires an explicitly local/test runtime seed created through authenticated APIs')
    test.skip(!(await hasApi('/api/v2/refresh', '/status')),
      'refresh operations API is unavailable')
    await loginAsAdmin(page)
  })

  test('shows the real refresh seed cursor, lag, and configuration version', async ({ page }) => {
    const seed = await readRefreshSeed()
    await page.goto(`/runtime/refresh/${seed.config_version.source_id}`)
    await expect(page.getByTestId('refresh-config-version')).toHaveText(String(seed.config_version.version))
    await expect(page.getByTestId('refresh-cursor')).toContainText(seed.cursor.primary_key)
    await expect(page.getByTestId('refresh-lag-seconds')).toHaveText(String(seed.lag.source_lag_seconds))
  })

  test('opens an explicitly provisioned runtime action plan without client-side seeding', async ({ page }) => {
    const planId = runtimeSeedId('plan')
    test.skip(!planId, 'set AGENT_E2E_RUNTIME_PLAN_ID after API-authenticated local/test provisioning')
    test.skip(!(await hasApi('/api/v2/runtime', '/action-plans')),
      'runtime API is unavailable')
    await page.goto(`/runtime/action-plans/${planId}`)
    await expect(page.getByTestId('plan-hash')).not.toBeEmpty()
  })

  test('opens an explicitly provisioned reconciliation case without client-side seeding', async ({ page }) => {
    const reconciliationId = runtimeSeedId('reconciliation')
    test.skip(!reconciliationId,
      'set AGENT_E2E_RUNTIME_RECONCILIATION_ID after API-authenticated local/test provisioning')
    test.skip(!(await hasApi('/api/v2/runtime', '/reconciliations')),
      'runtime API is unavailable')
    await page.goto(`/runtime/reconciliation/${reconciliationId}`)
    await expect(page.getByTestId('reconciliation-status')).toBeVisible()
  })
})
