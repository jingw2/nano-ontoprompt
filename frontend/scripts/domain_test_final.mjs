/**
 * nano-ontoprompt v2 — 全领域最终测试
 * 特性：时间戳命名避免冲突、短超时、完整截图流程
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const BASE_URL = 'http://localhost:5173';
const SCREENSHOT_DIR = './screenshots_final';
const RESULTS_FILE = './test_results_final.json';
const TEST_DATA_BASE = path.resolve('../test_data');
const TS = new Date().toISOString().slice(0,16).replace('T','_').replace(':','');

if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

const DOMAINS = [
  { id:'HR', nameCn:'人力资源', dir:'HR', domain:'其他',
    files:['performance_scores.csv','hr_policy.md','retention_risk.csv'],
    desc:'HR绩效管理与人才保留分析' },
  { id:'SupplyChain', nameCn:'供应链', dir:'供应链', domain:'供应链',
    files:['supply_chain_strategy.md','inventory_transactions.csv','logistics_performance.csv'],
    desc:'供应链策略与库存管理' },
  { id:'Medical', nameCn:'医疗', dir:'医疗', domain:'医疗',
    files:['clinical_protocols.md','adverse_events.csv','followup_records.csv'],
    desc:'临床协议与不良事件分析' },
  { id:'Education', nameCn:'教育', dir:'教育', domain:'教育',
    files:['academic_policy.md','attendance.csv','grade_records.csv'],
    desc:'学术政策与成绩分析' },
  { id:'Legal', nameCn:'法律', dir:'法律', domain:'法律',
    files:['legal_framework.md','ip_portfolio.csv','regulatory_changes.csv'],
    desc:'法律框架与知识产权管理' },
  { id:'Marketing', nameCn:'营销', dir:'营销', domain:'其他',
    files:['marketing_strategy.md','lead_data.csv','nps_survey.csv'],
    desc:'营销策略与客户满意度' },
  { id:'Finance', nameCn:'财务', dir:'财务', domain:'财务',
    files:['financial_controls.md','cash_flow.csv','expense_reports.csv'],
    desc:'财务控制与现金流分析' },
];

const sleep = ms => new Promise(r => setTimeout(r, ms));
const ss = async (page, name) => {
  const p = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: p, fullPage: false });
  return `${name}.png`;
};

async function login(page) {
  await page.goto(`${BASE_URL}/login`);
  await sleep(1500);
  await ss(page, '00_login');
  const inputs = page.locator('input');
  await inputs.nth(0).fill('admin');
  await inputs.nth(1).fill('admin123');
  await page.click('button[type="submit"], button:has-text("登录")');
  await sleep(2500);
  const url = page.url();
  const ok = !url.includes('/login');
  await ss(page, '01_after_login');
  console.log(ok ? '✅ 登录成功' : '❌ 登录失败，URL=' + url);
  return ok;
}

async function testMainPages(page) {
  const pages = [
    ['概览', '/overview', '02_overview'],
    ['数据管道-Connections', '/pipelines/connections', '03_connections'],
    ['数据管道-Datasets', '/pipelines/datasets', '04_datasets'],
    ['数据管道-Transforms', '/pipelines/transforms', '05_transforms'],
    ['数据管道-Curated', '/pipelines/curated', '06_curated'],
    ['本体列表', '/ontologies', '07_ontologies'],
    ['模型', '/models', '08_models'],
    ['设置', '/settings', '09_settings'],
  ];
  for (const [name, url, fname] of pages) {
    await page.goto(`${BASE_URL}${url}`);
    await sleep(1500);
    await ss(page, fname);
    console.log(`  📸 ${name}`);
  }
}

async function testDomain(page, domain, idx) {
  const pfx = `domain_${String(idx+1).padStart(2,'0')}_${domain.id}`;
  const ontName = `${domain.nameCn}测试_${TS}`;
  const r = {
    domain: domain.nameCn, domainEn: domain.id,
    ontologyName: ontName, ontologyId: null,
    status: 'FAIL', filesAttempted: domain.files.length, filesUploaded: 0,
    tabsAccessed: [], screenshots: {}, errors: [],
    startTime: Date.now(),
  };
  console.log(`\n[${idx+1}/7] 🧩 ${domain.nameCn} → "${ontName}"`);

  page.setDefaultTimeout(8000);

  try {
    // ── 1. 本体列表 ──────────────────────────────────────────
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(1500);
    r.screenshots.list = await ss(page, `${pfx}_01_list`);

    // ── 2. 创建本体 ──────────────────────────────────────────
    await page.click('button:has-text("创建本体")');
    await sleep(1000);
    r.screenshots.modal = await ss(page, `${pfx}_02_modal`);

    const modal = page.locator('.fixed.inset-0 .bg-white, div.fixed .bg-white').last();
    await modal.locator('input').first().fill(ontName);
    await sleep(200);
    await modal.locator('select').first().selectOption(domain.domain);
    await sleep(200);
    await modal.locator('textarea').first().fill(domain.desc);
    await sleep(200);
    r.screenshots.formFilled = await ss(page, `${pfx}_03_form`);

    await page.click('button:has-text("确认")');
    await sleep(3000);

    const url = page.url();
    const m = url.match(/\/ontologies\/([a-z0-9-]+)/);
    if (m) {
      r.ontologyId = m[1];
      console.log(`  ✅ 创建成功 ID=${r.ontologyId.slice(0,8)}`);
    } else {
      r.errors.push(`创建后未跳转详情页，URL=${url.slice(-30)}`);
    }

    r.screenshots.detail = await ss(page, `${pfx}_04_detail`);
    r.tabsAccessed.push('文件上传');

    // ── 3. 上传文件 ──────────────────────────────────────────
    const dataDir = path.join(TEST_DATA_BASE, domain.dir);
    for (const fname of domain.files) {
      const fpath = path.join(dataDir, fname);
      if (!fs.existsSync(fpath)) { r.errors.push(`缺失: ${fname}`); continue; }
      try {
        const fi = page.locator('input[type="file"]').first();
        await fi.setInputFiles(fpath);
        await sleep(2000);
        r.filesUploaded++;
        console.log(`  📄 上传: ${fname}`);
      } catch(e) {
        r.errors.push(`上传 ${fname}: ${e.message.slice(0,50)}`);
      }
    }
    r.screenshots.uploaded = await ss(page, `${pfx}_05_uploaded`);

    // ── 4. 知识图谱 ──────────────────────────────────────────
    try {
      await page.click('button:has-text("知识图谱")');
      await sleep(2000);
      r.screenshots.graph = await ss(page, `${pfx}_06_graph`);
      r.tabsAccessed.push('知识图谱');
    } catch(e) {}

    // ── 5. 实体 ─────────────────────────────────────────────
    try {
      await page.click('button:has-text("实体")');
      await sleep(1500);
      r.screenshots.entities = await ss(page, `${pfx}_07_entities`);
      r.tabsAccessed.push('实体');
    } catch(e) {}

    // ── 6. 逻辑规则 ─────────────────────────────────────────
    try {
      await page.click('button:has-text("逻辑规则")');
      await sleep(1200);
      r.screenshots.logic = await ss(page, `${pfx}_08_logic`);
      r.tabsAccessed.push('逻辑规则');
    } catch(e) {}

    // ── 7. 动作 ─────────────────────────────────────────────
    try {
      await page.click('button:has-text("动作")');
      await sleep(1200);
      r.screenshots.actions = await ss(page, `${pfx}_09_actions`);
      r.tabsAccessed.push('动作');
    } catch(e) {}

    // ── 8. LLM提取配置 ───────────────────────────────────────
    try {
      await page.click('button:has-text("LLM")');
      await sleep(1200);
      r.screenshots.llm = await ss(page, `${pfx}_10_llm`);
      r.tabsAccessed.push('LLM提取配置');
    } catch(e) {}

    r.status = r.ontologyId ? (r.filesUploaded > 0 ? 'PASS' : 'PARTIAL') : 'FAIL';
    console.log(`  结果: ${r.status} | 文件 ${r.filesUploaded}/${domain.files.length} | Tab: ${r.tabsAccessed.join(',')}`);

  } catch(err) {
    r.status = 'FAIL';
    r.errors.push(err.message.slice(0,100));
    console.log(`  ❌ ${err.message.slice(0,80)}`);
    await ss(page, `${pfx}_error`).catch(()=>{});
  }

  r.duration = Date.now() - r.startTime;
  return r;
}

async function main() {
  console.log('═══════════════════════════════════════════════');
  console.log('  nano-ontoprompt v2 — 全领域用户模拟测试 FINAL');
  console.log(`  时间戳: ${TS}`);
  console.log('═══════════════════════════════════════════════\n');

  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });
  const ctx = await browser.newContext({ viewport:{width:1440,height:900}, locale:'zh-CN' });
  const page = await ctx.newPage();
  page.setDefaultTimeout(8000);

  const results = { testTime: new Date().toISOString(), timestamp: TS, domains: [], summary:{} };

  try {
    const loggedIn = await login(page);
    if (!loggedIn) throw new Error('登录失败');

    await testMainPages(page);

    for (let i=0; i<DOMAINS.length; i++) {
      const r = await testDomain(page, DOMAINS[i], i);
      results.domains.push(r);
    }

    // 最终列表截图
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(2000);
    await ss(page, '99_final_list');
    console.log('\n📸 最终本体列表截图完成');
  } finally {
    await browser.close();
  }

  const pass = results.domains.filter(d=>d.status==='PASS').length;
  const partial = results.domains.filter(d=>d.status==='PARTIAL').length;
  const fail = results.domains.filter(d=>d.status==='FAIL').length;
  const totalUploaded = results.domains.reduce((s,d)=>s+d.filesUploaded,0);

  results.summary = {
    totalDomains:7, pass, partial, fail,
    filesUploaded: totalUploaded,
    filesAttempted: results.domains.reduce((s,d)=>s+d.filesAttempted,0),
    screenshotCount: fs.readdirSync(SCREENSHOT_DIR).length,
    avgDurationMs: Math.round(results.domains.reduce((s,d)=>s+d.duration,0)/7),
  };

  fs.writeFileSync(RESULTS_FILE, JSON.stringify(results,null,2));

  console.log('\n═══════════════════════════════════════════════');
  console.log('  测试完成');
  console.log(`  PASS:     ${pass}/7`);
  console.log(`  PARTIAL:  ${partial}/7`);
  console.log(`  FAIL:     ${fail}/7`);
  console.log(`  文件上传: ${totalUploaded}/${results.summary.filesAttempted}`);
  console.log(`  截图总数: ${results.summary.screenshotCount}`);
  console.log('═══════════════════════════════════════════════');
}

main().catch(e => { console.error(e); process.exit(1); });
