/**
 * models.spec.ts — model config CRUD. Self-skips until the models API is
 * registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

test.describe('Model Config Management', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/models')), 'backend /api/v1/models not registered yet')
    await loginAsAdmin(page)
    await page.goto('/models')
  })

  test('model list page loads', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('模型')
    await expect(page.locator('button:has-text("添加模型")')).toBeVisible()
  })

  test('create model config', async ({ page }) => {
    const name = `Test GPT-4-${Date.now()}`
    await page.click('button:has-text("添加模型")')
    await page.locator('input').first().fill(name)
    await page.fill('input[placeholder="https://api.openai.com/v1"]', 'https://api.openai.com/v1')
    await page.locator('textarea').first().fill('gpt-4o')
    await page.click('button:has-text("保存")')
    await expect(page.locator(`h3:has-text("${name}")`)).toBeVisible()
  })

  test('cancel model creation', async ({ page }) => {
    await page.click('button:has-text("添加模型")')
    await expect(page.locator('input').first()).toBeVisible()
    await page.click('button:has-text("取消")')
    // Modal should be closed - no more inputs visible
    await expect(page.locator('input[placeholder="https://api.openai.com/v1"]')).not.toBeVisible()
  })

  test('provider dropdown has OpenAI option', async ({ page }) => {
    await page.click('button:has-text("添加模型")')
    const select = page.locator('select').nth(1)
    await expect(select).toBeVisible()
    const options = await select.locator('option').allTextContents()
    expect(options.some(o => o.toLowerCase().includes('openai'))).toBeTruthy()
  })

  test('ocr config exposes EasyOCR provider', async ({ page }) => {
    await page.click('button:has-text("添加模型")')
    await page.locator('select').first().selectOption('ocr')
    const providerSelect = page.locator('select').nth(1)
    const options = await providerSelect.locator('option').allTextContents()
    expect(options).toContain('EasyOCR')
  })
})
