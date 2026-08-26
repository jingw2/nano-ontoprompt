/**
 * ontology_detail.spec.ts — ontology detail page tabs (info/files/entities/
 * logic/actions/export). Self-skips until the ontologies API is registered.
 */
import { test, expect, type Page } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

async function createOntology(page: Page): Promise<string> {
  await page.goto('/ontologies')
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/new$/)
  await page.click('button:has-text("简易 LLM 提取")')
  const name = `测试-${Date.now()}`
  await page.fill('input[placeholder="本体名称"]', name)
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+/)
  return name
}

test.describe('Ontology Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/ontologies')), 'backend /api/v1/ontologies not registered yet')
    await loginAsAdmin(page)
  })

  test('info tab shows basic info and LLM config sections', async ({ page }) => {
    // A newly created simple_llm ontology lands on the files tab; info is a
    // click away, not the default landing tab.
    await createOntology(page)
    await page.click('button:has-text("基本信息")')
    await expect(page.locator('h3:has-text("基本信息")')).toBeVisible()
    await expect(page.locator('h3:has-text("LLM 提取")')).toBeVisible()
  })

  test('switches to files tab', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("文件上传")')
    await expect(page.locator('text=拖拽文件')).toBeVisible()
  })

  test('switches to entities tab', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("实体")')
    await expect(page.locator('button:has-text("添加实体")')).toBeVisible()
  })

  test('create entity in entities tab', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("实体")')
    await page.click('button:has-text("添加实体")')
    await page.fill('input[placeholder="中文名 *"]', '测试实体')
    await page.fill('input[placeholder="英文名"]', 'TestEntity')
    await page.click('button:has-text("保存")')
    await expect(page.locator('text=测试实体')).toBeVisible()
  })

  test('switches to logic tab', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("逻辑规则")')
    await expect(page.locator('button:has-text("添加规则")')).toBeVisible()
  })

  test('switches to actions tab', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("动作")')
    await expect(page.locator('button:has-text("添加动作")')).toBeVisible()
  })

  test('export buttons visible', async ({ page }) => {
    await createOntology(page)
    // Export lives in the info tab, not the files tab creation lands on.
    await page.click('button:has-text("基本信息")')
    await expect(page.locator('button:has-text("JSON")')).toBeVisible()
    await expect(page.locator('button:has-text("YAML")')).toBeVisible()
    await expect(page.locator('button:has-text("CSV")')).toBeVisible()
  })

  test('back button navigates to list', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("← 返回")')
    await expect(page).toHaveURL(/\/ontologies$/)
  })
})
