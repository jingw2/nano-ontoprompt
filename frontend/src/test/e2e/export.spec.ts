/**
 * export.spec.ts — ontology JSON/YAML/CSV export. Self-skips until the
 * ontologies API is registered.
 */
import { test, expect, type Page } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

async function createOntology(page: Page): Promise<string> {
  await page.goto('/ontologies')
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/new$/)
  await page.click('button:has-text("简易 LLM 提取")')
  const name = `导出测试-${Date.now()}`
  await page.fill('input[placeholder="本体名称"]', name)
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+/)
  return name
}

test.describe('Export Functionality', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/ontologies')), 'backend /api/v1/ontologies not registered yet')
    await loginAsAdmin(page)
  })

  test('export links visible on ontology detail', async ({ page }) => {
    await createOntology(page)
    // Creation lands on the files tab; export lives in the info tab.
    await page.click('button:has-text("基本信息")')
    await expect(page.locator('button:has-text("JSON")')).toBeVisible()
    await expect(page.locator('button:has-text("YAML")')).toBeVisible()
    await expect(page.locator('button:has-text("CSV")')).toBeVisible()
  })

  test('JSON export triggers the export API call', async ({ page }) => {
    // The app builds the download from an in-page blob (URL.createObjectURL +
    // a synthetic <a download> click), which doesn't reliably fire
    // Playwright's `download` event in headless Chromium — asserting on the
    // real network response is the robust signal that export actually ran.
    await createOntology(page)
    await page.click('button:has-text("基本信息")')
    const responsePromise = page.waitForResponse(resp =>
      resp.url().includes('/export') && resp.url().includes('format=json'))
    await page.click('button:has-text("JSON")')
    const response = await responsePromise
    expect(response.ok()).toBeTruthy()
  })
})
