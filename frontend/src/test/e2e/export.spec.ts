import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:5173'

async function login(page: any) {
  await page.goto(`${BASE}/login`)
  await page.fill('input[placeholder="用户名"]', 'admin')
  await page.fill('input[placeholder="密码"]', 'admin123')
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/overview`)
}

async function createOntology(page: any): Promise<string> {
  await page.goto(`${BASE}/ontologies`)
  await page.click('button:has-text("创建 Ontology")')
  const name = `导出测试-${Date.now()}`
  await page.fill('input[placeholder="名称 *"]', name)
  await page.click('button:has-text("确认")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+$/)
  return name
}

test.describe('Export Functionality', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('export links visible on ontology detail', async ({ page }) => {
    await createOntology(page)
    await expect(page.locator('button:has-text("JSON")')).toBeVisible()
    await expect(page.locator('button:has-text("YAML")')).toBeVisible()
    await expect(page.locator('button:has-text("CSV")')).toBeVisible()
  })

  test('JSON export triggers download', async ({ page }) => {
    await createOntology(page)
    const downloadPromise = page.waitForEvent('download')
    await page.click('button:has-text("JSON")')
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/ontology_.*\.json/)
  })
})
