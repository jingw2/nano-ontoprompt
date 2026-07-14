import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function login(page: any) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

test.describe('Ontology List', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${BASE}/ontologies`)
  })

  test('ontology list page loads', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('Ontology')
    await expect(page.locator('button:has-text("创建 Ontology")')).toBeVisible()
  })

  test('create ontology modal opens', async ({ page }) => {
    await page.click('button:has-text("创建 Ontology")')
    await expect(page.locator('text=名称')).toBeVisible()
    await expect(page.locator('select')).toBeVisible()
  })

  test('create and view ontology', async ({ page }) => {
    const uniqueName = `测试本体-${Date.now()}`
    await page.click('button:has-text("创建 Ontology")')
    await page.fill('input[placeholder="名称 *"]', uniqueName)
    await page.click('button:has-text("确认")')
    await page.waitForURL(/\/ontologies\/[a-f0-9-]+$/)
    await expect(page.locator('h2')).toContainText(uniqueName)
  })

  test('filter ontologies by name', async ({ page }) => {
    const filter = page.locator('input[placeholder*="筛选"]')
    await filter.fill('不存在的本体xyz')
    await expect(page.locator('text=暂无 Ontology')).toBeVisible()
  })

  test('cancel delete dialog', async ({ page }) => {
    // First create one to delete
    await page.click('button:has-text("创建 Ontology")')
    await page.fill('input[placeholder="名称 *"]', `删除测试-${Date.now()}`)
    await page.click('button:has-text("确认")')
    await page.waitForURL(/\/ontologies\//)
    await page.goto(`${BASE}/ontologies`)

    const deleteBtn = page.locator('button:has-text("删除")').first()
    await deleteBtn.click()
    await expect(page.locator('h3:has-text("确认删除")')).toBeVisible()
    await page.click('button:has-text("取消")')
    await expect(page.locator('h3:has-text("确认删除")')).not.toBeVisible()
  })
})
