import { chromium } from '@playwright/test'
import { mkdirSync } from 'fs'

const BASE  = 'http://localhost:5173'
const OID   = '9da664ff-ead9-408c-9963-d54e77260dc9'
const SS_DIR = 'detail_screenshots'
mkdirSync(SS_DIR, { recursive: true })

let step = 0
async function shot(page, label) {
  step++
  const file = `${SS_DIR}/${String(step).padStart(2,'0')}_${label}.png`
  await page.screenshot({ path: file, fullPage: false })
  console.log(`📸  ${file}`)
}
async function wait(ms) { await new Promise(r => setTimeout(r, ms)) }

const browser = await chromium.launch({ headless: false, slowMo: 400 })
const ctx    = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page   = await ctx.newPage()

// Login
await page.goto(`${BASE}/login`)
await page.fill('input[placeholder="用户名"]', 'admin')
await page.fill('input[placeholder="密码"]', 'admin123')
await page.click('button[type="submit"]')
await page.waitForURL(`${BASE}/overview`)

// Navigate to the rich supply-chain ontology
await page.goto(`${BASE}/ontologies/${OID}`)
await page.waitForLoadState('networkidle')

// ─────────────────────────────────────────────────────────────────────────
// PART 1 — ENTITIES
// ─────────────────────────────────────────────────────────────────────────
await page.click('button:has-text("实体")')
await wait(600)
await shot(page, '01_entities_list_full')

// Open edit detail for "华为技术有限公司"
const huaweiRow = page.locator('tr', { hasText: '华为技术有限公司' })
await huaweiRow.locator('button').first().click()   // pencil icon
await wait(400)
await shot(page, '02_entity_detail_huawei')

// Close and open "iPhone 16"
await page.click('button:has-text("取消")')
await wait(300)
const iphoneRow = page.locator('tr', { hasText: 'iPhone 16' })
await iphoneRow.locator('button').first().click()
await wait(400)
await shot(page, '03_entity_detail_iphone16')

// Close and open "采购订单PO-2024"
await page.click('button:has-text("取消")')
await wait(300)
const poRow = page.locator('tr', { hasText: '采购订单PO-2024' })
await poRow.locator('button').first().click()
await wait(400)
await shot(page, '04_entity_detail_po2024')

await page.click('button:has-text("取消")')
await wait(300)

// ─────────────────────────────────────────────────────────────────────────
// PART 2 — LOGIC RULES
// ─────────────────────────────────────────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(600)
await shot(page, '05_logic_rules_list')

// Open edit for first logic rule
const rule1Row = page.locator('tr').nth(1)   // first data row
await rule1Row.locator('button').first().click()
await wait(400)
await shot(page, '06_logic_rule_detail_purchase_trigger')

await page.click('button:has-text("取消")')
await wait(300)

// Open second rule
const rule2Row = page.locator('tr').nth(2)
await rule2Row.locator('button').first().click()
await wait(400)
await shot(page, '07_logic_rule_detail_qc_pass')

await page.click('button:has-text("取消")')
await wait(300)

// Open third rule
const rule3Row = page.locator('tr').nth(3)
await rule3Row.locator('button').first().click()
await wait(400)
await shot(page, '08_logic_rule_detail_compliance')

await page.click('button:has-text("取消")')
await wait(300)

// ─────────────────────────────────────────────────────────────────────────
// PART 3 — ACTIONS
// ─────────────────────────────────────────────────────────────────────────
await page.click('button:has-text("动作")')
await wait(600)
await shot(page, '09_actions_list_4_actions')

// Open detail for "触发采购"
const action1Row = page.locator('tr', { hasText: '触发采购' })
await action1Row.locator('button').first().click()
await wait(400)
await shot(page, '10_action_detail_trigger_purchase')

await page.click('button:has-text("取消")')
await wait(300)

// Open detail for "质检审批"
const action2Row = page.locator('tr', { hasText: '质检审批' })
await action2Row.locator('button').first().click()
await wait(400)
await shot(page, '11_action_detail_qc_approval')

await page.click('button:has-text("取消")')
await wait(300)

// Open detail for "合同续签提醒"
const action3Row = page.locator('tr', { hasText: '合同续签提醒' })
await action3Row.locator('button').first().click()
await wait(400)
await shot(page, '12_action_detail_contract_renewal')

await page.click('button:has-text("取消")')
await wait(300)

// Open detail for "成本分析报告"
const action4Row = page.locator('tr', { hasText: '成本分析报告' })
await action4Row.locator('button').first().click()
await wait(400)
await shot(page, '13_action_detail_cost_analysis')

await page.click('button:has-text("取消")')
await wait(300)

// Final: show all three tabs side by side (overview of entities list again)
await page.click('button:has-text("实体")')
await wait(400)
await shot(page, '14_entities_overview_final')

await browser.close()
console.log(`\nDone — ${step} screenshots in ${SS_DIR}/`)
