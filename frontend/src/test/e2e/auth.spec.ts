import { test, expect, type Page } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function loginAs(page: Page, username = 'admin', password = 'admin123') {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', username)
  await page.fill('input[placeholder="密码"]', password)
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Authentication', () => {
  test('login page renders', async ({ page }) => {
    await page.goto(`${BASE}/login`)
    await expect(page.locator('h1')).toContainText('OntoPrompt')
    await expect(page.locator('button[type="submit"]')).toBeVisible()
  })

  test('redirects unauthenticated users to login', async ({ page }) => {
    await page.goto(`${BASE}/overview`)
    await expect(page).toHaveURL(/\/login/)
  })

  test('login with valid credentials', async ({ page }) => {
    await loginAs(page)
    await expect(page).toHaveURL(`${BASE}/overview`)
  })

  test('login with wrong password shows error', async ({ page }) => {
    await page.goto(`${BASE}/login`)
    await page.fill('input[placeholder="用户名"]', 'admin')
    await page.fill('input[placeholder="密码"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    await expect(page.locator('text=用户名或密码错误')).toBeVisible()
  })

  test('register page accessible', async ({ page }) => {
    await page.goto(`${BASE}/register`)
    await expect(page.locator('h1')).toContainText('注册')
  })

  test('logout redirects to login', async ({ page }) => {
    await loginAs(page)
    await page.click('button:has-text("退出")')
    await expect(page).toHaveURL(/\/login/)
  })

  test('language toggle works', async ({ page }) => {
    await loginAs(page)
    await page.click('button:has-text("EN")')
    await expect(page.locator('h2:has-text("Overview")')).toBeVisible()
    await page.click('button:has-text("中")')
    await expect(page.locator('h2:has-text("概览")')).toBeVisible()
  })
})
