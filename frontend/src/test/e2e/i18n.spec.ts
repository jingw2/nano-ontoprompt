import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function login(page: any) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Internationalization (i18n)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('default language is Chinese', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    // Language toggle should show "EN" when in Chinese mode
    await expect(page.locator('button:has-text("EN")')).toBeVisible()
  })

  test('language toggle switches to English', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await page.click('button:has-text("EN")')
    // After toggle, button should show Chinese option
    await expect(page.locator('button:has-text("中")')).toBeVisible()
  })

  test('English mode shows English nav labels', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await page.click('button:has-text("EN")')
    await expect(page.locator('h2:has-text("Overview")')).toBeVisible()
  })

  test('language persists after page reload', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await page.click('button:has-text("EN")')
    await page.reload()
    // Should still be in English
    await expect(page.locator('button:has-text("中")')).toBeVisible()
  })

  test('switching back to Chinese works', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await page.click('button:has-text("EN")')
    await page.click('button:has-text("中")')
    await expect(page.locator('button:has-text("EN")')).toBeVisible()
  })

  test('login page has Chinese labels', async ({ page }) => {
    await page.goto(`${BASE}/login`)
    await expect(page.locator('input[placeholder="用户名"]')).toBeVisible()
    await expect(page.locator('input[placeholder="密码"]')).toBeVisible()
  })
})
