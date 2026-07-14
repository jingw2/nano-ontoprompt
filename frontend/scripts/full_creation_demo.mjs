/**
 * Full ontology creation demo — simulates a real user:
 *   1. Login
 *   2. Create a brand-new ontology from scratch (through the UI)
 *   3. Upload 7 file types: xlsx, csv, docx, md, pptx, png, jpg
 *   4. Manually add 3 entities
 *   5. Manually add 2 logic rules
 *   6. Manually add 1 action
 *   7. View the knowledge graph
 *   8. Navigate into an entity detail page and edit it
 *   9. Navigate into a logic rule detail page
 *  10. Navigate into an action detail page
 */
import { chromium } from '@playwright/test'
import { mkdirSync } from 'fs'
import path from 'path'

const BASE   = 'http://localhost:5173'
const SS_DIR = 'full_creation_screenshots'
mkdirSync(SS_DIR, { recursive: true })

let step = 0
async function shot(page, label) {
  step++
  const file = `${SS_DIR}/${String(step).padStart(2,'0')}_${label}.png`
  await page.screenshot({ path: file, fullPage: false })
  console.log(`📸  ${file}`)
}
async function wait(ms) { await new Promise(r => setTimeout(r, ms)) }

const DATA = path.resolve('../test_data/documents')

const browser = await chromium.launch({ headless: false, slowMo: 350 })
const ctx     = await browser.newContext({ viewport: { width: 1280, height: 900 } })
const page    = await ctx.newPage()

// ── 1. Login ─────────────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`)
await page.waitForLoadState('networkidle')
await page.fill('input[placeholder="用户名"]', 'admin')
await page.fill('input[placeholder="密码"]', 'admin123')
await page.click('button[type="submit"]')
await page.waitForURL(`${BASE}/overview`)
await wait(600)
await shot(page, '01_logged_in_overview')

// ── 2. Open ontology list and click "新建本体" ────────────────────────────────
await page.goto(`${BASE}/ontologies`)
await page.waitForLoadState('networkidle')
await shot(page, '02_ontology_list_before_create')

await page.click('button:has-text("创建 Ontology")')
await wait(500)
await shot(page, '03_create_ontology_modal_open')

// Fill in the new ontology form
await page.fill('input[placeholder="名称 *"]', '医疗健康知识图谱')
await page.selectOption('select', '医疗')
await page.fill('textarea[placeholder="描述（可选）"]', '包含疾病、药品、医院、医生、检查项目等医疗实体及其关联关系，用于辅助临床决策')
await shot(page, '04_create_ontology_form_filled')
await page.click('button:has-text("确认")')
await page.waitForLoadState('networkidle')
await wait(800)
await shot(page, '05_new_ontology_info_tab')

// Capture the new ontology URL to use later
const ontologyUrl = page.url()
console.log(`  → New ontology URL: ${ontologyUrl}`)

// ── 3. Upload files — 7 different types ──────────────────────────────────────
await page.click('button:has-text("文件上传")')
await wait(500)
await shot(page, '06_files_tab_empty')

const uploads = [
  { file: 'supplier_data.xlsx',      label: '07_upload_xlsx' },
  { file: 'supplier_list.csv',       label: '08_upload_csv' },
  { file: 'supply_chain_policy.docx',label: '09_upload_docx' },
  { file: 'supply_chain.md',         label: '10_upload_md' },
  { file: 'ontology_overview.pptx',  label: '11_upload_pptx' },
  { file: 'sample_image.png',        label: '12_upload_png' },
  { file: 'sample_image.jpg',        label: '13_upload_jpg' },
]

for (const { file, label } of uploads) {
  const filePath = path.join(DATA, file)
  const [fc] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.click('.border-dashed'),
  ])
  await fc.setFiles(filePath)
  await wait(1800)
  await shot(page, label)
  console.log(`  → Uploaded: ${file}`)
}

await shot(page, '14_all_7_files_uploaded')

// ── 4. Entities tab — add 3 entities manually ────────────────────────────────
await page.click('button:has-text("实体")')
await wait(500)
await shot(page, '15_entities_tab_empty')

const entities = [
  { cn: '高血压', en: 'Hypertension', type: 'Disease',      desc: '慢性血压升高疾病', conf: '0.97' },
  { cn: '氨氯地平', en: 'Amlodipine',  type: 'Drug',         desc: '钙通道阻滞剂类降压药', conf: '0.95' },
  { cn: '心脏科门诊', en: 'Cardiology Clinic', type: 'Facility', desc: '专业心脏病诊疗科室', conf: '0.92' },
]

for (const [i, e] of entities.entries()) {
  await page.click('button:has-text("添加实体")')
  await wait(300)
  await page.fill('input[placeholder="中文名 *"]', e.cn)
  await page.fill('input[placeholder="英文名"]', e.en)
  await page.fill('input[placeholder="类型"]', e.type)
  await page.fill('textarea[placeholder="描述"]', e.desc)
  await page.fill('input[placeholder="置信度 (0-1)"]', e.conf)
  await shot(page, `16_entity_form_${i + 1}_${e.cn}`)
  await page.click('button:has-text("保存")')
  await wait(600)
  console.log(`  → Created entity: ${e.cn}`)
}

await shot(page, '17_three_entities_created')

// ── 5. Logic rules tab — add 2 rules ─────────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(500)
await shot(page, '18_logic_tab_empty')

const rules = [
  {
    cn: '高血压用药规则',
    en: 'HypertensionMedRule',
    formula: 'IF 诊断 = 高血压 AND 血压 >= 140/90 THEN 处方 = 氨氯地平 5mg',
    desc: '诊断高血压后触发降压药物处方',
    conf: '0.93',
  },
  {
    cn: '复诊提醒规则',
    en: 'FollowUpRule',
    formula: 'IF 上次就诊 > 90天 AND 慢性病标记 = true THEN 发送复诊提醒',
    desc: '慢性病患者超期未复诊时自动提醒',
    conf: '0.88',
  },
]

for (const [i, r] of rules.entries()) {
  await page.click('button:has-text("添加规则")')
  await wait(300)
  await page.fill('input[placeholder="中文名 *"]', r.cn)
  await page.fill('input[placeholder="英文名"]', r.en)
  await page.fill('input[placeholder="公式"]', r.formula)
  await page.fill('textarea[placeholder="描述"]', r.desc)
  await page.fill('input[placeholder="置信度 (0-1)"]', r.conf)
  await shot(page, `19_logic_form_${i + 1}_${r.cn}`)
  await page.click('button:has-text("保存")')
  await wait(600)
  console.log(`  → Created rule: ${r.cn}`)
}

await shot(page, '20_two_logic_rules_created')

// ── 6. Actions tab — add 1 action ────────────────────────────────────────────
await page.click('button:has-text("动作")')
await wait(500)
await shot(page, '21_actions_tab_empty')

await page.click('button:has-text("添加动作")')
await wait(300)
await page.fill('input[placeholder="中文名 *"]', '自动处方推送')
await page.fill('input[placeholder="英文名"]', 'AutoPrescriptionPush')
await page.fill('textarea[placeholder="执行规则"]', '当用药规则触发时，自动生成电子处方并推送至药房系统')
await page.fill('textarea[placeholder="描述"]', '将LLM生成的处方建议自动推送至医院HIS系统')
await page.fill('input[placeholder="置信度 (0-1)"]', '0.90')
await shot(page, '22_action_form_filled')
await page.click('button:has-text("保存")')
await wait(600)
await shot(page, '23_action_created')
console.log('  → Created action: 自动处方推送')

// ── 7. Graph tab — view the network ──────────────────────────────────────────
await page.click('button:has-text("图谱")')
await wait(3000)
await shot(page, '24_graph_tab_medical_ontology')

// ── 8. Navigate into Entity detail page ──────────────────────────────────────
await page.click('button:has-text("实体")')
await wait(600)

// Click first row to go to entity detail
const firstEntityRow = page.locator('tbody tr').first()
await firstEntityRow.click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '25_entity_detail_hypertension')

// Click Edit
await page.click('button:has-text("编辑")')
await wait(300)
// Update description
await page.locator('textarea').fill('高血压（Hypertension）是指以体循环动脉血压升高为主要特征，收缩压≥140mmHg和/或舒张压≥90mmHg')
await shot(page, '26_entity_edit_mode')
await page.click('button:has-text("保存")')
await wait(600)
await shot(page, '27_entity_detail_after_save')

// Back to entities tab
await page.click('button:has-text("返回实体列表")')
await wait(500)

// ── 9. Navigate into Logic rule detail ───────────────────────────────────────
await page.click('button:has-text("逻辑规则")')
await wait(600)

const firstRuleRow = page.locator('tbody tr').first()
await firstRuleRow.click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '28_logic_rule_detail_page')

// Back
await page.click('button:has-text("返回逻辑规则列表")')
await wait(400)

// ── 10. Navigate into Action detail ──────────────────────────────────────────
await page.click('button:has-text("动作")')
await wait(600)

const firstActionRow = page.locator('tbody tr').first()
await firstActionRow.click()
await page.waitForLoadState('networkidle')
await wait(500)
await shot(page, '29_action_detail_page')

// Back
await page.click('button:has-text("返回动作列表")')
await wait(400)

// ── 11. Info tab — export buttons ────────────────────────────────────────────
await page.click('button:has-text("信息")')
await wait(400)
await shot(page, '30_info_tab_final_state')

await browser.close()
console.log(`\n✅  Done — ${step} screenshots saved to ${SS_DIR}/`)
