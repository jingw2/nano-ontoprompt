import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function login(page: any) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Prompt Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${BASE}/prompts`)
  })

  test('prompt list page loads', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('Prompt')
    await expect(page.locator('button:has-text("创建 Prompt")')).toBeVisible()
  })

  test('shows domain filter', async ({ page }) => {
    await expect(page.locator('select')).toBeVisible()
  })

  test('create prompt', async ({ page }) => {
    await page.click('button:has-text("创建 Prompt")')
    await page.waitForURL(`${BASE}/prompts/create`)
    const name = `测试Prompt-${Date.now()}`
    await page.fill('input', name)
    await page.locator('textarea').fill('提取本体信息: {"entities": []}')
    await page.click('button:has-text("保存")')
    await page.waitForURL(`${BASE}/prompts`)
    await expect(page.locator(`text=${name}`)).toBeVisible()
  })

  test('built-in prompts are seeded', async ({ page }) => {
    // After login, built-in prompts should exist
    await expect(page.locator('text=通用本体提取')).toBeVisible()
  })
})
