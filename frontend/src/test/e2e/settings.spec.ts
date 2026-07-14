import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function login(page: any) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${BASE}/settings`)
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
