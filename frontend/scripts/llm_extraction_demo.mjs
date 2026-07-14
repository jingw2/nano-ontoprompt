/**
 * LLM extraction demo — correct OntoPrompt workflow.
 *
 * Setup (via API before browser):
 *   - Create a fresh ontology with timestamp name
 *   - Upload 4 supply chain documents (DOCX, MD, CSV, XLSX)
 *
 * Browser demo:
 *   1. Login → navigate to ontology
 *   2. Show files tab (uploaded documents)
 *   3. Info tab → expand extraction panel → select Prompt + Model → run LLM
 *   4. Poll until extraction completes
 *   5. Browse LLM-generated entities (with entity detail pages)
 *   6. Logic rules, Actions
 *   7. Knowledge graph with relations
 */
import { chromium } from '@playwright/test'
import { mkdirSync, rmSync } from 'fs'
import path from 'path'
import { execSync } from 'child_process'

const BASE   = 'http://localhost:5173'
const SS_DIR = 'llm_extraction_screenshots'
try { rmSync(SS_DIR, { recursive: true }) } catch {}
mkdirSync(SS_DIR, { recursive: true })

// ── Pre-setup via Python script ───────────────────────────────────────────────
console.log('  🔧 Creating ontology and uploading files via API...')
const setupResult = JSON.parse(execSync('python llm_setup.py', { encoding: 'utf8' }).trim())

const { oid, name: ontologyName, model_id, prompt_id, files } = setupResult
console.log(`  ✓ Ontology: ${ontologyName} (${oid})`)
console.log(`  ✓ Files uploaded: ${files.join(', ')}`)

// ── Browser session ───────────────────────────────────────────────────────────
let step = 0
async function shot(page, label) {
  step++
  const file = `${SS_DIR}/${String(step).padStart(2,'0')}_${label}.png`
  await page.screenshot({ path: file, fullPage: false })
  console.log(`📸  ${file}`)
}
async function wait(ms) { await new Promise(r => setTimeout(r, ms)) }

const browser = await chromium.launch({ headless: false, slowMo: 300 })
const ctx     = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page    = await ctx.newPage()

// ── 1. Login ──────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`)
await page.waitForLoadState('networkidle')
await page.fill('input[placeholder="用户名"]', 'admin')
await page.fill('input[placeholder="密码"]', 'admin123')
await page.click('button[type="submit"]')
await page.waitForURL(`${BASE}/overview`)
await wait(400)
await shot(page, '01_overview')

// ── 2. Navigate to the new ontology ──────────────────────────────────────────
await page.goto(`${BASE}/ontologies/${oid}`)
await page.waitForLoadState('networkidle')
await page.waitForSelector('button:has-text("基本信息")', { timeout: 10000 })
await wait(400)
await shot(page, '02_ontology_info_tab')

// ── 3. Files tab — show uploaded documents ────────────────────────────────────
await page.click('button:has-text("文件上传")')
await wait(800)
await shot(page, '03_files_tab_4_documents_ready')

// ── 4. Info tab → configure and start LLM extraction ─────────────────────────
await page.click('button:has-text("基本信息")')
await wait(400)
await shot(page, '04_info_tab_before_extraction')

await page.click('button:has-text("开始提取")')
await wait(500)
await shot(page, '05_extraction_panel_open')

// Select 供应链本体提取 prompt
await page.locator('select').nth(0).selectOption({ label: '供应链本体提取 (供应链)' })
await wait(300)
await shot(page, '06_prompt_selected')

// Select DeepSeek model config
await page.locator('select').nth(1).selectOption({ label: 'DeepSeek V4 Flash (compatible)' })
await wait(400)
await shot(page, '07_model_config_selected')

// Select deepseek-chat as specific model
await page.locator('select').nth(2).selectOption('deepseek-chat')
await wait(300)
await shot(page, '08_model_name_selected_all_ready')

// ── 5. Fire the extraction ────────────────────────────────────────────────────
await page.locator('button.bg-blue-600').click()
await wait(600)
await shot(page, '09_extraction_started_queued')
console.log('  ⏳ DeepSeek LLM extraction running...')

// Poll status box until done/failed
let done = false
for (let i = 0; i < 40; i++) {
  await wait(4000)
  const boxes = await page.locator('.bg-gray-50').all()
  if (boxes.length === 0) {
    console.log(`  [${i+1}] Status box gone — extraction likely completed`)
    done = true; break
  }
  const txt = await boxes[boxes.length - 1].textContent().catch(() => '')
  const short = txt?.replace(/\s+/g, ' ').trim().substring(0, 100)
  console.log(`  [${i+1}] ${short}`)
  if (txt?.includes('completed') || txt?.includes('100%')) {
    done = true
    await shot(page, `10_extraction_completed`)
    break
  }
  if (txt?.includes('failed')) {
    console.log('  ❌ Extraction failed')
    await shot(page, '10_extraction_failed')
    break
  }
  if (i % 4 === 0) await shot(page, `10_progress_${i+1}`)
}

console.log(done ? '  ✅ Extraction done!' : '  ⚠️  Timed out — checking results')
await wait(800)
await shot(page, '11_after_extraction_status')

// ── 6. Entities tab ───────────────────────────────────────────────────────────
await page.click('button:has-text("实体")')
await wait(800)
await shot(page, '12_entities_tab_llm_results')

const entityCount = await page.locator('tbody tr').count()
console.log(`  → ${entityCount} entities extracted by LLM`)

for (let i = 0; i < Math.min(3, entityCount); i++) {
  await page.locator('tbody tr').nth(i).click()
  await page.waitForLoadState('networkidle')
  await wait(400)
  await shot(page, `13_entity_detail_${i + 1}`)
  await page.click('button:has-text("返回实体列表")')
  await wait(400)
}

// ── 7. Logic rules tab ────────────────────────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(600)
await shot(page, '14_logic_rules_llm_results')
const ruleCount = await page.locator('tbody tr').count()
console.log(`  → ${ruleCount} logic rules extracted by LLM`)

if (ruleCount > 0) {
  await page.locator('tbody tr').first().click()
  await page.waitForLoadState('networkidle')
  await wait(400)
  await shot(page, '15_logic_rule_detail')
  await page.click('button:has-text("返回逻辑规则列表")')
  await wait(400)
}

// ── 8. Actions tab ────────────────────────────────────────────────────────────
await page.click('button:has-text("动作")')
await wait(600)
await shot(page, '16_actions_llm_results')
const actionCount = await page.locator('tbody tr').count()
console.log(`  → ${actionCount} actions extracted by LLM`)

if (actionCount > 0) {
  await page.locator('tbody tr').first().click()
  await page.waitForLoadState('networkidle')
  await wait(400)
  await shot(page, '17_action_detail')
  await page.click('button:has-text("返回动作列表")')
  await wait(400)
}

// ── 9. Graph tab — knowledge graph with relations ─────────────────────────────
await page.click('button:has-text("图谱")')
await wait(3500)
await shot(page, '18_knowledge_graph_with_relations')

// ── 10. Final info tab ────────────────────────────────────────────────────────
await page.click('button:has-text("基本信息")')
await wait(400)
await shot(page, '19_final_info_export')

await browser.close()
console.log(`\n✅  Done — ${step} screenshots in ${SS_DIR}/`)
console.log(`   Extracted: ${entityCount} entities, ${ruleCount} logic rules, ${actionCount} actions`)
