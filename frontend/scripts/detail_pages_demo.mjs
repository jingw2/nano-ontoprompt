/**
 * Detail pages demo — navigate into entity / logic / action detail pages,
 * edit them, and return to the list.
 *
 * Targets the latest 46-entity supply chain ontology.
 */
import { chromium } from '@playwright/test'
import { mkdirSync, rmSync } from 'fs'

const BASE   = 'http://localhost:5173'
const OID    = '6af1850d-11d0-4302-a8cf-d24dcc3b30da'
const SS_DIR = 'detail_pages_screenshots'
try { rmSync(SS_DIR, { recursive: true }) } catch {}
mkdirSync(SS_DIR, { recursive: true })

let step = 0
async function shot(page, label) {
  step++
  const file = `${SS_DIR}/${String(step).padStart(2,'0')}_${label}.png`
  await page.screenshot({ path: file, fullPage: false })
  console.log(`📸  ${file}`)
}
async function wait(ms) { await new Promise(r => setTimeout(r, ms)) }

const browser = await chromium.launch({ headless: false, slowMo: 350 })
const ctx     = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page    = await ctx.newPage()

// Login
await page.goto(`${BASE}/login`)
await page.fill('input[placeholder="用户名"]', 'admin')
await page.fill('input[placeholder="密码"]', 'admin123')
await page.click('button[type="submit"]')
await page.waitForURL(`${BASE}/overview`)

// Go to the supply-chain ontology
await page.goto(`${BASE}/ontologies/${OID}`)
await page.waitForLoadState('networkidle')
await wait(500)

// ── ENTITIES ──────────────────────────────────────────────────────────────────
await page.click('button:has-text("实体")')
await wait(700)
await shot(page, '01_entities_list_46_items')

// Click first row → entity detail page
await page.locator('tbody tr').nth(0).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '02_entity_detail_supplier')

// Edit the entity
await page.click('button:has-text("编辑")')
await wait(300)
await shot(page, '03_entity_edit_mode')

// Change description
const descTA = page.locator('textarea').first()
await descTA.fill('天钢原材料有限公司，专注于钢铁及铝合金原材料供应，ISO 9001:2015认证，供货周期7-14工作日')
await wait(200)
await page.click('button:has-text("保存")')
await wait(600)
await shot(page, '04_entity_after_save')

// Back to list
await page.click('button:has-text("返回实体列表")')
await wait(500)

// Click second entity (different type if possible)
await page.locator('tbody tr').nth(10).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '05_entity_detail_product_or_category')

// Back to list
await page.click('button:has-text("返回实体列表")')
await wait(400)

// Click third entity
await page.locator('tbody tr').nth(20).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '06_entity_detail_third')

await page.click('button:has-text("返回实体列表")')
await wait(400)
await shot(page, '07_entities_list_back')

// ── LOGIC RULES ───────────────────────────────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(700)
await shot(page, '08_logic_rules_list_6_rules')

// Click first logic rule
await page.locator('tbody tr').nth(0).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '09_logic_rule_detail_safety_stock')

// Edit the rule
await page.click('button:has-text("编辑")')
await wait(300)
await shot(page, '10_logic_rule_edit_mode')

// Save without changes
await page.click('button:has-text("保存")')
await wait(600)
await shot(page, '11_logic_rule_after_save')

await page.click('button:has-text("返回逻辑规则列表")')
await wait(400)

// Click second rule
await page.locator('tbody tr').nth(1).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '12_logic_rule_detail_second')

await page.click('button:has-text("返回逻辑规则列表")')
await wait(400)

// Click third rule
await page.locator('tbody tr').nth(2).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '13_logic_rule_detail_third')

await page.click('button:has-text("返回逻辑规则列表")')
await wait(400)
await shot(page, '14_logic_rules_list_back')

// ── ACTIONS ───────────────────────────────────────────────────────────────────
await page.click('button:has-text("动作")')
await wait(700)
await shot(page, '15_actions_list_4_actions')

// Click first action
await page.locator('tbody tr').nth(0).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '16_action_detail_auto_restock')

// Edit the action
await page.click('button:has-text("编辑")')
await wait(300)
await shot(page, '17_action_edit_mode')

await page.click('button:has-text("保存")')
await wait(600)
await shot(page, '18_action_after_save')

await page.click('button:has-text("返回动作列表")')
await wait(400)

// Click second action
await page.locator('tbody tr').nth(1).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '19_action_detail_second')

await page.click('button:has-text("返回动作列表")')
await wait(400)

// Click third action
await page.locator('tbody tr').nth(2).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '20_action_detail_third')

await page.click('button:has-text("返回动作列表")')
await wait(400)

// Click fourth action
await page.locator('tbody tr').nth(3).click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '21_action_detail_fourth')

await page.click('button:has-text("返回动作列表")')
await wait(400)
await shot(page, '22_actions_list_final')

await browser.close()
console.log(`\n✅  Done — ${step} screenshots in ${SS_DIR}/`)
