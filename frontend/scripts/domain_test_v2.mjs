/**
 * nano-ontoprompt v2 — 全领域前端用户模拟测试（修正版）
 * 基于真实 UI 结构：创建本体 modal → Files 标签上传 → 查看各 Tab
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const BASE_URL = 'http://localhost:5173';
const API_URL = 'http://localhost:8000';
const SCREENSHOT_DIR = './screenshots_v2';
const RESULTS_FILE = './test_results_v2.json';
const TEST_DATA_BASE = path.resolve('../test_data');

if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

// 7 个测试领域配置
const DOMAINS = [
  {
    id: 'HR', nameCn: '人力资源', dir: 'HR', domain: '其他',
    files: ['performance_scores.csv', 'hr_policy.md', 'retention_risk.csv'],
    description: 'HR 绩效管理与人才保留分析',
  },
  {
    id: 'SupplyChain', nameCn: '供应链', dir: '供应链', domain: '供应链',
    files: ['supply_chain_strategy.md', 'inventory_transactions.csv', 'logistics_performance.csv'],
    description: '供应链策略与库存管理',
  },
  {
    id: 'Medical', nameCn: '医疗', dir: '医疗', domain: '医疗',
    files: ['clinical_protocols.md', 'adverse_events.csv', 'followup_records.csv'],
    description: '临床协议与不良事件分析',
  },
  {
    id: 'Education', nameCn: '教育', dir: '教育', domain: '教育',
    files: ['academic_policy.md', 'attendance.csv', 'grade_records.csv'],
    description: '学术政策与成绩分析',
  },
  {
    id: 'Legal', nameCn: '法律', dir: '法律', domain: '法律',
    files: ['legal_framework.md', 'ip_portfolio.csv', 'regulatory_changes.csv'],
    description: '法律框架与知识产权管理',
  },
  {
    id: 'Marketing', nameCn: '营销', dir: '营销', domain: '其他',
    files: ['marketing_strategy.md', 'lead_data.csv', 'nps_survey.csv'],
    description: '营销策略与客户满意度',
  },
  {
    id: 'Finance', nameCn: '财务', dir: '财务', domain: '财务',
    files: ['financial_controls.md', 'cash_flow.csv', 'expense_reports.csv'],
    description: '财务控制与现金流分析',
  },
];

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function ss(page, name) {
  const p = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

// ── 登录 ────────────────────────────────────────────────────────────────
async function login(page) {
  await page.goto(`${BASE_URL}/login`);
  await sleep(2000);
  await ss(page, '00_login');

  // 填写用户名和密码
  const inputs = page.locator('input');
  const count = await inputs.count();
  if (count >= 2) {
    await inputs.nth(0).fill('admin');
    await inputs.nth(1).fill('admin123');
  }
  await ss(page, '01_login_filled');

  await page.keyboard.press('Enter');
  await sleep(3000);
  await ss(page, '02_after_login');
  console.log('✅ 登录完成');
}

// ── 主页面截图 ────────────────────────────────────────────────────────
async function screenshotMainPages(page) {
  // Overview
  await page.goto(`${BASE_URL}/overview`);
  await sleep(2000);
  await ss(page, '03_overview');

  // Pipelines - Connections
  await page.goto(`${BASE_URL}/pipelines/connections`);
  await sleep(2000);
  await ss(page, '04_pipelines_connections');

  // Pipelines - Datasets
  await page.goto(`${BASE_URL}/pipelines/datasets`);
  await sleep(1500);
  await ss(page, '05_pipelines_datasets');

  // Pipelines - Transforms
  await page.goto(`${BASE_URL}/pipelines/transforms`);
  await sleep(1500);
  await ss(page, '06_pipelines_transforms');

  // Pipelines - Curated
  await page.goto(`${BASE_URL}/pipelines/curated`);
  await sleep(1500);
  await ss(page, '07_pipelines_curated');

  // Ontology List
  await page.goto(`${BASE_URL}/ontologies`);
  await sleep(2000);
  await ss(page, '08_ontologies_list');

  // Models
  await page.goto(`${BASE_URL}/models`);
  await sleep(1500);
  await ss(page, '09_models');

  // Settings
  await page.goto(`${BASE_URL}/settings`);
  await sleep(1500);
  await ss(page, '10_settings');

  console.log('✅ 主页面截图完成');
}

// ── 单领域测试 ────────────────────────────────────────────────────────
async function testDomain(page, domain, idx) {
  const pfx = `domain_${String(idx+1).padStart(2,'0')}_${domain.id}`;
  console.log(`\n[${idx+1}/7] 🧩 ${domain.nameCn}`);

  const result = {
    domain: domain.nameCn,
    domainEn: domain.id,
    ontologyDomain: domain.domain,
    status: 'FAIL',
    ontologyId: null,
    filesAttempted: domain.files.length,
    filesUploaded: 0,
    tabsTested: [],
    screenshots: {},
    pageLoadTime: 0,
    errors: [],
    startTime: Date.now(),
  };

  try {
    // 1. 本体列表页
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(2000);
    await ss(page, `${pfx}_01_list`);
    result.screenshots.list = `${pfx}_01_list.png`;

    // 2. 点击"创建本体"按钮
    await page.click('button:has-text("创建本体")');
    await sleep(1500);
    await ss(page, `${pfx}_02_modal`);
    result.screenshots.createModal = `${pfx}_02_modal.png`;

    // 3. 在弹窗内填写本体名称（使用弹窗容器约束选择器）
    const modal = page.locator('.fixed.inset-0, [class*="modal"], div.bg-white.rounded-lg.shadow-lg').last();
    const nameInput = modal.locator('input').first();
    await nameInput.fill(`${domain.nameCn}测试本体`);
    await sleep(300);

    // 4. 选择领域（弹窗内的 select）
    const domainSelect = modal.locator('select').first();
    await domainSelect.selectOption(domain.domain);
    await sleep(300);

    // 5. 填写描述（弹窗内的 textarea）
    const textarea = modal.locator('textarea').first();
    await textarea.fill(domain.description);
    await sleep(300);

    await ss(page, `${pfx}_03_form_filled`);
    result.screenshots.formFilled = `${pfx}_03_form_filled.png`;

    // 6. 点击"确认"
    await page.click('button:has-text("确认")');
    await sleep(3000);

    // 7. 等待跳转到详情页
    const url = page.url();
    const m = url.match(/\/ontologies\/([a-zA-Z0-9-]+)/);
    if (m) {
      result.ontologyId = m[1];
      console.log(`  ID: ${result.ontologyId}`);
    }

    await ss(page, `${pfx}_04_detail_info`);
    result.screenshots.detail = `${pfx}_04_detail_info.png`;
    result.tabsTested.push('Info');

    // 8. 上传文件（详情页默认在"文件上传"标签）
    await sleep(1000);
    await ss(page, `${pfx}_05_files_tab`);
    result.tabsTested.push('文件上传');
    result.screenshots.filesTab = `${pfx}_05_files_tab.png`;

    // 逐个上传文件
    const dataDir = path.join(TEST_DATA_BASE, domain.dir);
    for (const fname of domain.files) {
      const fpath = path.join(dataDir, fname);
      if (!fs.existsSync(fpath)) {
        result.errors.push(`文件不存在: ${fname}`);
        continue;
      }
      try {
        const fileInput = page.locator('input[type="file"]').first();
        await fileInput.setInputFiles(fpath);
        await sleep(3000);  // 等待上传完成
        result.filesUploaded++;
        console.log(`  📄 上传: ${fname}`);
      } catch(e) {
        result.errors.push(`上传失败 ${fname}: ${e.message.substring(0,60)}`);
      }
    }
    await ss(page, `${pfx}_06_files_uploaded`);
    result.screenshots.filesUploaded = `${pfx}_06_files_uploaded.png`;

    // 9. 知识图谱标签
    const t1 = Date.now();
    try {
      const graphTab = page.locator('button:has-text("知识图谱"), a:has-text("知识图谱")').first();
      await graphTab.click();
      await sleep(2500);
      await ss(page, `${pfx}_07_graph`);
      result.screenshots.graph = `${pfx}_07_graph.png`;
      result.tabsTested.push('知识图谱');
    } catch(e) { result.errors.push(`图谱标签: ${e.message.substring(0,60)}`); }

    // 10. 实体标签
    try {
      const entTab = page.locator('button:has-text("实体"), a:has-text("实体")').first();
      await entTab.click();
      await sleep(1500);
      await ss(page, `${pfx}_08_entities`);
      result.screenshots.entities = `${pfx}_08_entities.png`;
      result.tabsTested.push('实体');
    } catch(e) {}

    // 11. 逻辑规则标签
    try {
      const logicTab = page.locator('button:has-text("逻辑规则"), a:has-text("逻辑规则")').first();
      await logicTab.click();
      await sleep(1500);
      await ss(page, `${pfx}_09_logic`);
      result.screenshots.logic = `${pfx}_09_logic.png`;
      result.tabsTested.push('逻辑规则');
    } catch(e) {}

    // 12. 动作标签
    try {
      const actTab = page.locator('button:has-text("动作"), a:has-text("动作")').first();
      await actTab.click();
      await sleep(1500);
      await ss(page, `${pfx}_10_actions`);
      result.screenshots.actions = `${pfx}_10_actions.png`;
      result.tabsTested.push('动作');
    } catch(e) {}

    result.pageLoadTime = Date.now() - t1;
    result.status = result.filesUploaded > 0 ? 'PASS' :
                    result.ontologyId ? 'PARTIAL' : 'FAIL';
    console.log(`  ✅ 完成 | 上传: ${result.filesUploaded}/${domain.files.length} | Tab: ${result.tabsTested.join(',')}`);

  } catch(err) {
    result.status = 'FAIL';
    result.errors.push(err.message.substring(0, 100));
    console.log(`  ❌ ${err.message.substring(0, 80)}`);
    await ss(page, `${pfx}_error`);
    result.screenshots.error = `${pfx}_error.png`;
  }

  result.duration = Date.now() - result.startTime;
  return result;
}

// ── 最终列表截图 ─────────────────────────────────────────────────────
async function finalScreenshot(page) {
  await page.goto(`${BASE_URL}/ontologies`);
  await sleep(2500);
  await ss(page, '99_final_list');
  console.log('\n✅ 最终列表截图完成');
}

// ── 主流程 ────────────────────────────────────────────────────────────
async function main() {
  console.log('═══════════════════════════════════════════════════');
  console.log('  nano-ontoprompt v2 — 全领域模拟用户测试 v2');
  console.log('═══════════════════════════════════════════════════\n');

  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  });
  const page = await ctx.newPage();

  const results = { testTime: new Date().toISOString(), domains: [], pages: {}, summary: {} };

  try {
    await login(page);
    await screenshotMainPages(page);

    for (let i = 0; i < DOMAINS.length; i++) {
      const r = await testDomain(page, DOMAINS[i], i);
      results.domains.push(r);
    }

    await finalScreenshot(page);
  } finally {
    await browser.close();
  }

  // 统计
  const pass = results.domains.filter(d => d.status === 'PASS').length;
  const partial = results.domains.filter(d => d.status === 'PARTIAL').length;
  const fail = results.domains.filter(d => d.status === 'FAIL').length;
  const totalUploaded = results.domains.reduce((s, d) => s + d.filesUploaded, 0);
  const totalAttempted = results.domains.reduce((s, d) => s + d.filesAttempted, 0);

  results.summary = {
    totalDomains: 7, pass, partial, fail,
    filesUploaded: totalUploaded, filesAttempted: totalAttempted,
    screenshotCount: fs.readdirSync(SCREENSHOT_DIR).length,
  };

  fs.writeFileSync(RESULTS_FILE, JSON.stringify(results, null, 2));

  console.log('\n═══════════════════════════════════════════════════');
  console.log('  测试完成汇总');
  console.log('═══════════════════════════════════════════════════');
  console.log(`  通过:     ${pass}/7`);
  console.log(`  部分通过: ${partial}/7`);
  console.log(`  失败:     ${fail}/7`);
  console.log(`  文件上传: ${totalUploaded}/${totalAttempted}`);
  console.log(`  截图数量: ${results.summary.screenshotCount}`);
  console.log(`  结果文件: ${RESULTS_FILE}`);
  console.log('═══════════════════════════════════════════════════');
}

main().catch(e => { console.error(e); process.exit(1); });
