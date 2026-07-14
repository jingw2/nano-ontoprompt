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
  const name = `测试-${Date.now()}`
  await page.fill('input[placeholder="名称 *"]', name)
  await page.click('button:has-text("确认")')
  await page.waitForURL(/\/ontologies\/[a-f0-9-]+$/)
  return name
}

test.describe('Ontology Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('shows info tab by default', async ({ page }) => {
    await createOntology(page)
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
    await expect(page.locator('button:has-text("JSON")')).toBeVisible()
    await expect(page.locator('button:has-text("YAML")')).toBeVisible()
    await expect(page.locator('button:has-text("CSV")')).toBeVisible()
  })

  test('back button navigates to list', async ({ page }) => {
    await createOntology(page)
    await page.click('button:has-text("← 返回")')
    await expect(page).toHaveURL(`${BASE}/ontologies`)
  })
})
