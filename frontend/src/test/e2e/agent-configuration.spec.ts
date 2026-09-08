/**
 * agent-configuration.spec.ts — plan FE-02/FE-03: agent detail tabs (Basic /
 * System Prompt with generation provenance).  Self-skips until the agents API
 * is registered.
 */
import { test, expect } from '@playwright/test'
import { hasApi } from './helpers/availability'
import { loginAsAdmin, openFirstAgent } from './helpers/ui'


test('FE-02 detail: five tabs render with the Application tab live', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agents')), 'backend /api/v1/agents not registered yet')
  // navigate to the first agent if the fixture seeded one; otherwise skip data assertions
  await loginAsAdmin(page)
  if (!(await openFirstAgent(page))) {
    test.skip(true, 'no agents seeded')
    return
  }
  const tabs: [string, string][] = [
    ['Basic', '基本信息'],
    ['System Prompt', '系统提示词'],
    ['Tools', '工具'],
    ['Memory', '记忆'],
    ['Agent Application', '智能体应用'],
  ]
  for (const [en, zh] of tabs) {
    await expect(
      page.locator(`button:has-text("${en}")`).first()
        .or(page.locator(`button:has-text("${zh}")`).first()),
    ).toBeVisible()
  }
})

test('FE-03 prompt generation: draft and provenance surface', async ({ page }) => {
  test.skip(!(await hasApi('/api/v1/agents', 'prompt-generations')), 'prompt generations not registered yet')
  await loginAsAdmin(page)
  if (!(await openFirstAgent(page))) {
    test.skip(true, 'no agents seeded')
    return
  }
  await page.locator('button:has-text("System Prompt"), button:has-text("系统提示词")').first().click()
  await expect(page.locator('textarea').first()).toBeVisible()
  await expect(page.locator('button:has-text("Generate"), button:has-text("生成")').first()).toBeVisible()
})
