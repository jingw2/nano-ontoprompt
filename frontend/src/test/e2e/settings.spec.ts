/**
 * settings.spec.ts — extraction confidence-rules settings page. Self-skips
 * until the settings API is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/settings')), 'backend /api/v1/settings not registered yet')
    await loginAsAdmin(page)
    await page.goto('/settings')
  })

  test('settings page loads', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('设置')
  })

  test('shows extraction rules section', async ({ page }) => {
    await expect(page.locator('button:has-text("置信度规则")')).toBeVisible()
  })

  test('confidence threshold inputs exist', async ({ page }) => {
    await expect(page.locator('input').first()).toBeVisible()
  })

  test('save settings button exists', async ({ page }) => {
    await expect(page.locator('button:has-text("保存")')).toBeVisible()
  })
})
