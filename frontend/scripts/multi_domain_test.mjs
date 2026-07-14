/**
 * 多业务域本体提取测试：
 * 财务 / 医疗 / 营销 / HR 四个领域，每个配不同文件集 + prompt，
 * 提取后验证质量指标并截图。
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE  = 'http://localhost:5173';
const API   = 'http://localhost:8002/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_domains');
const DOCS  = path.join(__dirname, '../test_data/documents');

mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss    = (domain, label) => path.join(SSDIR,
  `${String(++step).padStart(3,'0')}_[${domain}]_${label.replace(/[^\w一-龥]/g,'_').slice(0,35)}.png`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const r = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? [];
}

// ─── 领域配置 ──────────────────────────────────────────────────────────────
const DOMAINS = [
  {
    name:    '财务',
    domain:  '财务',
    desc:    '财务本体：资产负债、收入费用、现金流、采购订单、仓库库存多源数据',
    prompt:  '财务本体提取',
    files: [
      'finance_report.md',
      'purchase_orders.csv',
      'warehouse_inventory.xlsx',
    ],
  },
  {
    name:    '医疗',
    domain:  '医疗',
    desc:    '医疗本体：疾病、药物、症状、治疗方案、诊疗流程',
    prompt:  '医疗本体提取',
    files: [
      'medical_ontology.md',
    ],
  },
  {
    name:    '营销',
    domain:  '其他',
    desc:    '营销本体：客户分层、渠道矩阵、产品定价、销售漏斗、竞品分析',
    prompt:  '通用本体提取',
    files: [
      'marketing_strategy.md',
    ],
  },
  {
    name:    'HR',
    domain:  '其他',
    desc:    'HR本体：组织架构、招聘体系、薪酬绩效、培训发展、员工关系',
    prompt:  '通用本体提取',
    files: [
      'hr_talent.md',
      'supplier_list.csv',   // 借用作人员清单参照
    ],
  },
];

async function waitExtraction(page, maxMin = 10) {
  for (let i = 0; i < maxMin * 60 / 4; i++) {
    await sleep(4000);
    const pct = await page.locator('p').filter({ hasText: '%' }).first().textContent().catch(() => '');
    process.stdout.write(`\r    进度: ${pct?.trim() ?? '?'}        `);
    if (await page.locator('.bg-green-500').count() > 0) { console.log('\n    ✅ 完成'); return 'ok'; }
    if (await page.locator('text=提取失败').count() > 0) { console.log('\n    ❌ 失败'); return 'fail'; }
  }
  console.log('\n    ⏰ 超时');
  return 'timeout';
}

async function runDomain(page, token, cfg, results) {
  const { name, domain, desc, prompt, files } = cfg;
  console.log(`\n${'═'.repeat(60)}`);
  console.log(`  领域: ${name}  |  Prompt: ${prompt}  |  文件: ${files.length} 个`);
  console.log(`${'═'.repeat(60)}`);

  // 1. 创建本体
  await page.goto(`${BASE}/ontologies`);
  await page.waitForLoadState('networkidle');
  await page.click('button:has-text("创建本体")');
  await sleep(400);
  const runId = Date.now();
  await page.fill('input[placeholder="名称 *"]', `${name}域-${runId}`);
  await page.selectOption('select', domain);
  await page.fill('textarea', desc);
  await page.click('button:has-text("确认")');
  await page.waitForURL(/\/ontologies\/[a-f0-9-]{36}$/, { timeout: 10000 });
  const oid = page.url().split('/').pop();
  console.log(`  本体 ID: ${oid}`);
  await sleep(400);
  await page.screenshot({ path: ss(name, '创建成功') });

  // 2. 上传文件
  await page.waitForSelector('input[type="file"]', { timeout: 8000 });
  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.setInputFiles(files.map(f => path.join(DOCS, f)));
  console.log(`  上传 ${files.length} 个文件...`);
  await sleep(5000);
  const rowCount = await page.locator('table tbody tr').count();
  console.log(`  文件列表行数: ${rowCount}`);
  await page.screenshot({ path: ss(name, '文件上传') });

  // 3. LLM 配置
  const infoBtn = page.locator('button').filter({ hasText: /LLM提取配置|信息/ }).first();
  await infoBtn.click().catch(() => {});
  await sleep(500);

  const selects = page.locator('select');
  const promptOpts = await selects.nth(0).locator('option').allTextContents();
  const targetPrompt = promptOpts.find(t => t.includes(prompt));
  if (targetPrompt) {
    await selects.nth(0).selectOption({ label: targetPrompt });
    console.log(`  ✓ Prompt: ${targetPrompt.trim()}`);
  } else {
    // fallback to first non-empty
    console.log(`  ⚠ 未找到 "${prompt}"，使用第一个可用`);
    if (promptOpts.length > 1) await selects.nth(0).selectOption({ index: 1 });
  }
  await sleep(400);

  const modelOpts = await selects.nth(1).locator('option').allTextContents();
  const deepOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
  if (deepOpt) {
    await selects.nth(1).selectOption({ label: deepOpt });
  } else if (modelOpts.length > 1) {
    await selects.nth(1).selectOption({ index: 1 });
  }
  await sleep(600);

  const sc = await page.locator('select').count();
  if (sc >= 3) {
    const mnOpts = await page.locator('select').nth(2).locator('option').allTextContents();
    const mn = mnOpts.find(t => t.toLowerCase().includes('deepseek')) ?? (mnOpts.length > 1 ? mnOpts[1] : null);
    if (mn) await page.locator('select').nth(2).selectOption({ label: mn });
    await sleep(300);
  }
  await page.screenshot({ path: ss(name, 'LLM配置') });

  // 4. 开始提取
  const btn = page.locator('button:has-text("开始提取")');
  if (await btn.isDisabled()) {
    console.log('  ❌ 提取按钮禁用');
    results.push({ name, oid, status: 'config_error', entities: 0, logic: 0, actions: 0 });
    return;
  }
  await btn.click();
  console.log('  ✓ 已触发提取');
  await sleep(1500);
  await page.locator('text=提取进度').scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: ss(name, '提取进度开始') });

  const extractResult = await waitExtraction(page, 10);
  await page.screenshot({ path: ss(name, '提取完成') });

  if (extractResult !== 'ok') {
    results.push({ name, oid, status: extractResult, entities: 0, logic: 0, actions: 0 });
    return;
  }

  // P0 报告
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(400);
  const reportCard = page.locator('text=P0 输出质量报告');
  if (await reportCard.count() > 0) {
    await reportCard.scrollIntoViewIfNeeded();
    await sleep(300);
    await page.screenshot({ path: ss(name, 'P0质量报告') });
    const badge = await page.locator('text=/\\d+ 个问题|完美通过|质量通过/').first().textContent().catch(() => '');
    console.log(`  P0: ${badge.trim()}`);
  }

  // 5. API 质量数据
  const entities = await apiGet(`/ontologies/${oid}/entities`, token);
  const logic    = await apiGet(`/ontologies/${oid}/logic`, token);
  const actions  = await apiGet(`/ontologies/${oid}/actions`, token);
  const graph    = await apiGet(`/ontologies/${oid}/graph`, token);

  const withProps   = entities.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
  const withLE      = logic.filter(r => (r.linked_entities ?? []).length > 0).length;
  const withCode    = actions.filter(a => a.function_code?.trim().length > 10).length;

  results.push({
    name, oid, status: 'ok',
    entities: entities.length, withProps,
    logic: logic.length, withLE,
    actions: actions.length, withCode,
    edges: graph.edges?.length ?? 0,
  });

  console.log(`  实体: ${entities.length}（有属性: ${withProps}）  逻辑: ${logic.length}（有关联实体: ${withLE}）  动作: ${actions.length}（有代码: ${withCode}）  图谱边: ${graph.edges?.length ?? 0}`);

  // 6. 各 tab 截图
  for (const tab of ['实体', '逻辑规则', '动作', '知识图谱']) {
    const tabBtn = page.locator('button').filter({ hasText: tab });
    if (await tabBtn.count() === 0) continue;
    await tabBtn.first().click();
    await sleep(tab === '知识图谱' ? 3000 : 1200);
    await page.screenshot({ path: ss(name, tab) });
  }

  // 7. 实体详情（第一个有属性的实体）
  const eWithProp = entities.find(e => Object.keys(e.properties ?? {}).length > 0);
  if (eWithProp) {
    await page.goto(`${BASE}/ontologies/${oid}/entities/${eWithProp.id}`);
    await sleep(1200);
    await page.screenshot({ path: ss(name, `实体详情_${eWithProp.name_cn}`), fullPage: true });
    console.log(`  实体样例: ${eWithProp.name_cn} | 属性: ${JSON.stringify(eWithProp.properties).slice(0,80)}`);
  }

  // 8. 动作详情（第一个有 function_code 的动作）
  const aWithCode = actions.find(a => a.function_code?.trim().length > 10);
  if (aWithCode) {
    await page.goto(`${BASE}/ontologies/${oid}/actions/${aWithCode.id}`);
    await sleep(1200);
    await page.screenshot({ path: ss(name, `动作详情_${aWithCode.name_cn}`), fullPage: true });
    const codeLine = aWithCode.function_code.trim().split('\n')[0];
    console.log(`  动作样例: ${aWithCode.name_cn} | 代码首行: ${codeLine.slice(0,60)}`);
  }
}

// ─── 主流程 ────────────────────────────────────────────────────────────────
(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 35 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('token') || '');
  console.log('✓ 已登录');

  const results = [];
  for (const cfg of DOMAINS) {
    await runDomain(page, token, cfg, results);
    await page.goto(`${BASE}/overview`);
    await sleep(600);
  }

  // ─── 总览对比 ──────────────────────────────────────────────────────────
  await page.goto(`${BASE}/overview`);
  await sleep(1200);
  await page.screenshot({ path: ss('ALL', '总览页_四域提取后') });

  console.log('\n\n' + '═'.repeat(70));
  console.log('  多域提取质量汇总');
  console.log('═'.repeat(70));
  console.log(`  ${'领域'.padEnd(6)} ${'实体'.padStart(4)} ${'有属性'.padStart(6)} ${'逻辑'.padStart(4)} ${'有关联'.padStart(6)} ${'动作'.padStart(4)} ${'有代码'.padStart(6)} ${'图谱边'.padStart(6)} 状态`);
  console.log('  ' + '-'.repeat(65));
  for (const r of results) {
    if (r.status !== 'ok') {
      console.log(`  ${r.name.padEnd(6)} ${'—'.padStart(4)} ${'—'.padStart(6)} ${'—'.padStart(4)} ${'—'.padStart(6)} ${'—'.padStart(4)} ${'—'.padStart(6)} ${'—'.padStart(6)} ❌ ${r.status}`);
    } else {
      console.log(`  ${r.name.padEnd(6)} ${String(r.entities).padStart(4)} ${String(r.withProps).padStart(6)} ${String(r.logic).padStart(4)} ${String(r.withLE).padStart(6)} ${String(r.actions).padStart(4)} ${String(r.withCode).padStart(6)} ${String(r.edges).padStart(6)} ✅`);
    }
  }
  console.log('═'.repeat(70));
  console.log(`\n📸 共 ${step} 张截图 → ${SSDIR}`);

  await browser.close();
})().catch(e => { console.error('❌', e.message, '\n', e.stack); process.exit(1); });
