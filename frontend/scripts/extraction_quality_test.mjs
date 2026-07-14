/**
 * 提取质量验证测试：
 * 验证使用"供应链本体提取" prompt + DeepSeek 提取后，
 * 实体有属性(properties)，逻辑规则有关联实体，动作有关联实体和关联逻辑规则。
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API = 'http://localhost:8002/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_quality');
const DOCS = path.join(__dirname, '../test_data/documents');

import { mkdirSync } from 'fs';
mkdirSync(SSDIR, { recursive: true });

let step = 0;
const SS = (label) => {
  const file = path.join(SSDIR, `q${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g, '_').slice(0,30)}.png`);
  return file;
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Helper: call API directly to inspect raw data
async function apiGet(path, token) {
  const res = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  const j = await res.json();
  return j.data ?? j;
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 50 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  let token = '';

  // ─── 1. 登录，获取 token ──────────────────────────────────────────────────────
  console.log('\n=== 1. 登录 ===');
  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  await sleep(500);

  // Grab token from localStorage
  token = await page.evaluate(() => localStorage.getItem('token') || sessionStorage.getItem('token') || '');
  if (!token) {
    // Try getting it from the auth cookie/header via network
    const cookies = await ctx.cookies();
    console.log('  cookies:', cookies.map(c => c.name));
  }
  console.log('  token:', token ? `${token.slice(0,20)}...` : '未获取到（将通过UI验证）');
  await page.screenshot({ path: SS('登录成功') });

  // ─── 2. 创建新 Ontology ──────────────────────────────────────────────────────
  console.log('\n=== 2. 创建 Ontology ===');
  await page.goto(`${BASE}/ontologies`);
  await page.waitForLoadState('networkidle');
  await page.click('button:has-text("创建本体")');
  await sleep(500);
  const runId = Date.now();
  await page.fill('input[placeholder="名称 *"]', `质量测试-${runId}`);
  await page.selectOption('select', '供应链');
  await page.fill('textarea', '用于验证提取质量：实体属性、逻辑规则关联实体、动作关联实体和规则');
  await page.click('button:has-text("确认")');
  await page.waitForURL(/\/ontologies\/[a-f0-9-]{36}$/, { timeout: 10000 });
  const oid = page.url().split('/').pop();
  console.log('  Ontology ID:', oid);
  await sleep(500);
  await page.screenshot({ path: SS('创建成功') });

  // ─── 3. 上传文件 ─────────────────────────────────────────────────────────────
  console.log('\n=== 3. 文件上传 ===');
  await page.waitForSelector('input[type="file"]', { timeout: 8000 });
  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.setInputFiles([
    path.join(DOCS, 'supply_chain.md'),
    path.join(DOCS, 'supplier_list.csv'),
    path.join(DOCS, 'procurement_management.docx'),
  ]);
  console.log('  上传 3 个文件...');
  await sleep(5000);
  const rowCount = await page.locator('table tbody tr').count();
  console.log(`  文件列表: ${rowCount} 行`);
  await page.screenshot({ path: SS('文件上传完成') });

  // ─── 4. LLM 配置 ─────────────────────────────────────────────────────────────
  console.log('\n=== 4. LLM 提取配置 ===');
  await page.click('button:has-text("LLM提取配置")');
  await sleep(600);

  const selects = page.locator('select');
  const sCount = await selects.count();

  // 选供应链本体提取 prompt
  const promptOpts = await selects.nth(0).locator('option').allTextContents();
  const supplyOpt = promptOpts.find(t => t.includes('供应链本体提取'));
  if (supplyOpt) {
    await selects.nth(0).selectOption({ label: supplyOpt });
    console.log('  ✓ 选择 Prompt:', supplyOpt.trim());
  } else {
    console.log('  ⚠️ 未找到"供应链本体提取"，可用:', promptOpts.slice(0,5));
  }
  await sleep(400);

  // 选 DeepSeek 模型
  if (sCount >= 2) {
    const modelOpts = await selects.nth(1).locator('option').allTextContents();
    const deepseekOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (deepseekOpt) {
      await selects.nth(1).selectOption({ label: deepseekOpt });
      console.log('  ✓ 选择模型:', deepseekOpt.trim());
    }
    await sleep(600);
  }

  const sCountNow = await page.locator('select').count();
  if (sCountNow >= 3) {
    const mnOpts = await page.locator('select').nth(2).locator('option').allTextContents();
    const deepseekModel = mnOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (deepseekModel) {
      await page.locator('select').nth(2).selectOption({ label: deepseekModel });
      console.log('  ✓ 选择模型名:', deepseekModel.trim());
    }
    await sleep(300);
  }
  await page.screenshot({ path: SS('LLM配置完成') });

  // ─── 5. 开始提取 ─────────────────────────────────────────────────────────────
  console.log('\n=== 5. 开始提取 ===');
  const btn = page.locator('button:has-text("开始提取")');
  const disabled = await btn.isDisabled();
  if (disabled) {
    console.log('  ❌ 提取按钮禁用！检查配置');
    await browser.close();
    process.exit(1);
  }

  await btn.click();
  console.log('  ✓ 提取已触发');
  await sleep(1500);
  await page.locator('text=提取进度').scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: SS('提取进度开始') });

  // 轮询
  let done = false;
  for (let i = 0; i < 60; i++) {
    await sleep(4000);
    const pct = await page.locator('p:has-text("%")').first().textContent().catch(() => '');
    process.stdout.write(`\r  进度: ${pct?.trim() ?? ''}        `);
    if (await page.locator('.bg-green-500').count() > 0) { console.log('\n  ✅ 提取成功'); done = true; break; }
    if (await page.locator('text=提取失败').count() > 0) { console.log('\n  ❌ 提取失败'); break; }
  }
  await page.screenshot({ path: SS('提取完成状态') });
  if (!done) { await browser.close(); process.exit(1); }

  // ─── 5b. P0 质量报告（提取完成后立即截图，此时 taskStatus 还在内存中）───────────
  console.log('\n=== 5b. P0 质量报告 ===');
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(600);
  const reportCard = page.locator('text=P0 输出质量报告');
  if (await reportCard.count() > 0) {
    await reportCard.scrollIntoViewIfNeeded();
    await sleep(400);
    await page.screenshot({ path: SS('P0质量报告') });
    const issueText = await page.locator('text=/\\d+ 个问题|完美通过|质量通过/').first().textContent().catch(() => '');
    console.log(`  P0 报告: ${issueText.trim()}`);
  } else {
    console.log('  ⚠️ P0 报告卡片未找到，可能 validation_report 未写入');
    await page.screenshot({ path: SS('P0报告未找到') });
  }

  // ─── 6. 检查实体（UI + API）─────────────────────────────────────────────────
  console.log('\n=== 6. 检查实体质量 ===');
  await page.click('button:has-text("实体")');
  await sleep(1500);
  await page.screenshot({ path: SS('实体列表') });

  // 点第一个实体
  const entityLink = page.locator('a[href*="/entities/"]').first();
  if (await entityLink.count() > 0) {
    await entityLink.click();
    await sleep(1000);
    await page.screenshot({ path: SS('实体详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: SS('实体详情_底部_关联规则和动作') });
    await page.goBack(); await sleep(500);
  }

  // API 验证
  if (token) {
    const entities = await apiGet(`/ontologies/${oid}/entities`, token);
    console.log(`\n  📦 提取实体数: ${entities.length}`);
    let withProps = 0, withLogic = 0, withActions = 0;
    for (const e of entities.slice(0, 10)) {
      const props = e.properties ?? {};
      const hasProps = Object.keys(props).length > 0;
      if (hasProps) withProps++;
      console.log(`  - ${e.name_cn} [${e.type}] | 属性数: ${Object.keys(props).length} | properties: ${JSON.stringify(props).slice(0, 80)}`);
    }
    console.log(`\n  有属性的实体: ${withProps}/${Math.min(entities.length, 10)}`);
  }

  // ─── 7. 检查逻辑规则（UI + API）─────────────────────────────────────────────
  console.log('\n=== 7. 检查逻辑规则质量 ===');
  await page.click('button:has-text("逻辑规则")');
  await sleep(1200);
  await page.screenshot({ path: SS('逻辑规则列表') });

  const logicLink = page.locator('a[href*="/logic/"]').first();
  if (await logicLink.count() > 0) {
    await logicLink.click();
    await sleep(1000);
    await page.screenshot({ path: SS('逻辑规则详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: SS('逻辑规则详情_底部_关联实体和动作') });
    await page.goBack(); await sleep(500);
  }

  if (token) {
    const rules = await apiGet(`/ontologies/${oid}/logic`, token);
    console.log(`\n  ⚖️ 逻辑规则数: ${rules.length}`);
    let withEntities = 0;
    for (const r of rules) {
      const le = r.linked_entities ?? [];
      if (le.length > 0) withEntities++;
      console.log(`  - ${r.name_cn} | 关联实体: [${le.join(', ')}]`);
    }
    console.log(`\n  有关联实体的规则: ${withEntities}/${rules.length}`);
  }

  // ─── 8. 检查动作（UI + API）─────────────────────────────────────────────────
  console.log('\n=== 8. 检查动作质量 ===');
  await page.click('button:has-text("动作")');
  await sleep(1200);
  await page.screenshot({ path: SS('动作列表') });

  const actionLink = page.locator('a[href*="/actions/"]').first();
  if (await actionLink.count() > 0) {
    await actionLink.click();
    await sleep(1000);
    await page.screenshot({ path: SS('动作详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: SS('动作详情_底部_关联实体和规则') });
    await page.goBack(); await sleep(500);
  }

  if (token) {
    const actions = await apiGet(`/ontologies/${oid}/actions`, token);
    console.log(`\n  ⚡ 动作数: ${actions.length}`);
    let withEntities = 0, withLogic = 0, withCode = 0;
    for (const a of actions) {
      const le = a.linked_entities ?? [];
      const ll = a.linked_logic_ids ?? [];
      const hasCode = !!(a.function_code && a.function_code.trim().length > 10);
      if (le.length > 0) withEntities++;
      if (ll.length > 0) withLogic++;
      if (hasCode) withCode++;
      const codePreview = hasCode ? a.function_code.trim().split('\n')[0] : '（无）';
      console.log(`  - ${a.name_cn} | 关联实体: [${le.join(', ')}] | 关联逻辑: ${ll.length} 条 | 代码首行: ${codePreview}`);
    }
    console.log(`\n  有关联实体的动作: ${withEntities}/${actions.length}`);
    console.log(`  有关联逻辑规则的动作: ${withLogic}/${actions.length}`);
    console.log(`  有 function_code 的动作: ${withCode}/${actions.length}`);
  }

  // ─── 8b. 查看动作详情（含 function_code 展示）────────────────────────────────
  if (await actionLink.count() > 0) {
    await page.click('button:has-text("动作")');
    await sleep(800);
    const firstAction = page.locator('a[href*="/actions/"]').first();
    if (await firstAction.count() > 0) {
      await firstAction.click();
      await sleep(1000);
      await page.screenshot({ path: SS('动作详情_function_code') });
      await page.goBack(); await sleep(400);
    }
  }

  // ─── 9. 知识图谱 ─────────────────────────────────────────────────────────────
  console.log('\n=== 9. 知识图谱 ===');
  await page.click('button:has-text("知识图谱")');
  await sleep(3000);
  await page.screenshot({ path: SS('知识图谱') });

  console.log(`\n\n✅ 提取质量测试完成！共 ${step} 张截图已保存至 screenshots_quality/`);
  await browser.close();
})().catch(e => {
  console.error('\n\n❌ 测试失败:', e.message);
  process.exit(1);
});
