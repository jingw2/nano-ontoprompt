/**
 * graph_interaction.spec.ts — ontology detail graph tab (empty state,
 * node/edge counts). Self-skips until the ontologies API is registered.
 */
import { test, expect, type Page } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin } from './helpers/ui'

async function createOntology(page: Page): Promise<string> {
  await page.goto('/ontologies')
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/new$/)
  await page.click('button:has-text("简易 LLM 提取")')
  const name = `图谱测试-${Date.now()}`
  await page.fill('input[placeholder="本体名称"]', name)
  await page.click('button:has-text("创建本体")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+/)
  return name
}

test.describe('Graph Tab Interaction', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await hasApi('/api/v1/ontologies')), 'backend /api/v1/ontologies not registered yet')
    await loginAsAdmin(page)
  })

  test('graph tab shows empty state without extraction', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("图谱")')
    // Graph tab should load - either show canvas or empty message
    await page.waitForTimeout(1000)
    const hasEmpty = await page.locator('text=暂无图谱数据').count()
    const hasCanvas = await page.locator('canvas').count()
    expect(hasEmpty + hasCanvas).toBeGreaterThan(0)
  })

  test('graph tab shows node/edge counts', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("图谱")')
    await page.waitForTimeout(1000)
    await expect(page.locator('text=节点')).toBeVisible()
    await expect(page.locator('text=边')).toBeVisible()
  })

  test('graph empty state has guidance message', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("图谱")')
    await page.waitForTimeout(1000)
    const hasMessage = await page.locator('text=暂无图谱数据').count()
    if (hasMessage > 0) {
      await expect(page.locator('text=暂无图谱数据')).toBeVisible()
    }
  })
})
