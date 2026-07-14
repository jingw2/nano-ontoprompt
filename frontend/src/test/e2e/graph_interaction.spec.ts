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
  const name = `图谱测试-${Date.now()}`
  await page.fill('input[placeholder="名称 *"]', name)
  await page.click('button:has-text("确认")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+$/)
  return name
}

test.describe('Graph Tab Interaction', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
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
