/**
 * ontology_list.spec.ts — ontology list page (create/filter/delete). Self-
 * skips until the ontologies API is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

test.describe('Ontology List', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/ontologies')), 'backend /api/v1/ontologies not registered yet')
    await loginAsAdmin(page)
    await page.goto('/ontologies')
  })

  test('ontology list page loads', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('本体管理')
    await expect(page.locator('button:has-text("创建本体")')).toBeVisible()
  })

  test('create button opens the build-mode selection wizard', async ({ page }) => {
    await page.click('button:has-text("创建本体")')
    await page.waitForURL(/\/ontologies\/new$/)
    await expect(page.locator('button:has-text("简易 LLM 提取")')).toBeVisible()
    await expect(page.locator('button:has-text("Pipeline Mapping")')).toBeVisible()
  })

  test('create and view ontology', async ({ page }) => {
    const uniqueName = `测试本体-${Date.now()}`
    await page.click('button:has-text("创建本体")')
    await page.waitForURL(/\/ontologies\/new$/)
    await page.click('button:has-text("简易 LLM 提取")')
    await page.fill('input[placeholder="本体名称"]', uniqueName)
    await page.click('button:has-text("创建本体")')
    await page.waitForURL(/\/ontologies\/[a-f0-9-]+/)
    await expect(page.locator('h2')).toContainText(uniqueName)
  })

  test('filter ontologies by name', async ({ page }) => {
    const filter = page.locator('input[placeholder*="筛选"]')
    await filter.fill('不存在的本体xyz')
    await expect(page.locator('text=没有符合筛选条件的本体')).toBeVisible()
  })

  test('cancel delete dialog', async ({ page }) => {
    // First create one to delete
    await page.click('button:has-text("创建本体")')
    await page.waitForURL(/\/ontologies\/new$/)
    await page.click('button:has-text("简易 LLM 提取")')
    await page.fill('input[placeholder="本体名称"]', `删除测试-${Date.now()}`)
    await page.click('button:has-text("创建本体")')
    await page.waitForURL(/\/ontologies\/[a-f0-9-]+/)
    await page.goto('/ontologies')

    const deleteBtn = page.locator('button:has-text("删除")').first()
    await deleteBtn.click()
    await expect(page.locator('h3:has-text("确认删除")')).toBeVisible()
    await page.click('button:has-text("取消")')
    await expect(page.locator('h3:has-text("确认删除")')).not.toBeVisible()
  })
})
