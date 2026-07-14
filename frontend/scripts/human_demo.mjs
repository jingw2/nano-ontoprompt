import { chromium } from '@playwright/test'
import { mkdirSync } from 'fs'
import path from 'path'

const BASE = 'http://localhost:5173'
const GRAPH_OID = '9da664ff-ead9-408c-9963-d54e77260dc9'
const SS_DIR = 'human_demo_screenshots'
mkdirSync(SS_DIR, { recursive: true })

let step = 0
async function shot(page, label) {
  step++
  const file = `${SS_DIR}/${String(step).padStart(2,'0')}_${label}.png`
  await page.screenshot({ path: file, fullPage: false })
  console.log(`📸  ${file}`)
  return file
}

async function wait(ms) { await new Promise(r => setTimeout(r, ms)) }

async function closeModalIfOpen(page) {
  const overlay = page.locator('.fixed.inset-0')
  if (await overlay.isVisible().catch(() => false)) {
    // click 取消 button inside the modal
    const cancel = overlay.locator('button:has-text("取消")')
    if (await cancel.isVisible().catch(() => false)) {
      await cancel.click()
    }
    await wait(400)
  }
}

const browser = await chromium.launch({ headless: false, slowMo: 500 })
const ctx    = await browser.newContext({ viewport: { width: 1280, height: 800 } })
const page   = await ctx.newPage()

// ── 1. Login ──────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`)
await page.waitForLoadState('networkidle')
await shot(page, 'login_page')
await page.fill('input[placeholder="用户名"]', 'admin')
await page.fill('input[placeholder="密码"]', 'admin123')
await page.click('button[type="submit"]')
await page.waitForURL(`${BASE}/overview`)
await page.waitForLoadState('networkidle')
await shot(page, 'overview_dashboard')

// ── 2. Language toggle ────────────────────────────────────────────────────
await page.click('button:has-text("EN")')
await wait(600)
await shot(page, 'overview_english')
await page.click('button:has-text("中")')
await wait(600)

// ── 3. Navigate to rich graph ontology ───────────────────────────────────
await page.goto(`${BASE}/ontologies/${GRAPH_OID}`)
await page.waitForLoadState('networkidle')
await shot(page, 'ontology_info_tab')

// ── 4. Entities tab ───────────────────────────────────────────────────────
await page.click('button:has-text("实体")')
await wait(600)
await shot(page, 'entities_tab_14_entities')

// ── 5. Logic rules tab ────────────────────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(600)
await shot(page, 'logic_rules_with_4_rules')

// ── 6. FILE UPLOAD TAB — upload a real XLSX ───────────────────────────────
await page.click('button:has-text("文件上传")')
await wait(600)
await shot(page, 'files_tab_before_upload')

// Use file chooser to upload the supplier xlsx
const xlsxPath = path.resolve('../test_data/documents/supplier_data.xlsx')
const [fileChooser] = await Promise.all([
  page.waitForEvent('filechooser'),
  page.click('.border-dashed'),   // click the dropzone
])
await fileChooser.setFiles(xlsxPath)
await wait(2000)   // wait for upload
await shot(page, 'files_tab_after_upload_xlsx')

// Upload a second file (policy docx)
const docxPath = path.resolve('../test_data/documents/supply_chain_policy.docx')
const [fc2] = await Promise.all([
  page.waitForEvent('filechooser'),
  page.click('.border-dashed'),
])
await fc2.setFiles(docxPath)
await wait(2000)
await shot(page, 'files_tab_two_files')

// ── 7. GRAPH TAB — rich network ───────────────────────────────────────────
await page.click('button:has-text("图谱")')
await wait(3000)   // let cytoscape layout settle
await shot(page, 'graph_14_nodes_18_edges')

// click a node to show detail
await wait(500)
const graphContainer = page.locator('.bg-white.border.rounded-lg').last()
await graphContainer.click({ position: { x: 300, y: 200 } })
await wait(400)
await shot(page, 'graph_node_selected')

// ── 8. Back to info for export buttons ───────────────────────────────────
await page.click('button:has-text("信息")')
await wait(400)
await shot(page, 'info_tab_export_buttons')

// ── 9. Prompts page ───────────────────────────────────────────────────────
await page.goto(`${BASE}/prompts`)
await page.waitForLoadState('networkidle')
await shot(page, 'prompts_list')

// ── 10. Models page — show DeepSeek ───────────────────────────────────────
await page.goto(`${BASE}/models`)
await page.waitForLoadState('networkidle')
await shot(page, 'models_with_deepseek')

// Test the DeepSeek connection
const testBtn = page.locator('button[title="测试连接"]').first()
if (await testBtn.isVisible()) {
  await testBtn.click()
  await wait(4000)   // wait for API response
  await shot(page, 'deepseek_connection_test')
}

// ── 11. Settings page ─────────────────────────────────────────────────────
await page.goto(`${BASE}/settings`)
await page.waitForLoadState('networkidle')
await shot(page, 'settings_page')

// ── 12. Ontology list ─────────────────────────────────────────────────────
await page.goto(`${BASE}/ontologies`)
await page.waitForLoadState('networkidle')
await shot(page, 'ontology_list_all')

// ── 13. Final overview ───────────────────────────────────────────────────
await page.goto(`${BASE}/overview`)
await page.waitForLoadState('networkidle')
await shot(page, 'final_overview')

await browser.close()
console.log(`\nDone — ${step} screenshots saved to ${SS_DIR}/`)
