/**
 * Re-extract 财务 / 营销 / HR with new domain-specific prompts.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE  = 'http://localhost:5173';
const API   = 'http://localhost:8002/api/v1';
const SSDIR = 'E:/零点未来/137. nanoontology/ontoprompt/frontend/screenshots_reextract';
mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss    = (d, l) => `${SSDIR}/${String(++step).padStart(3,'0')}_[${d}]_${l.replace(/[^\w一-龥]/g,'_').slice(0,35)}.png`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const r = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? [];
}

const TARGETS = [
  { name: '财务', oid: 'ee16adcf-ca0d-44b8-8698-67c7e8242dec', prompt: '财务本体提取' },
  { name: '营销', oid: '5234b7f3-2e3d-4536-b2ce-f48508b596a4', prompt: '营销本体提取' },
  { name: 'HR',   oid: '75d5e5f7-c101-49a6-a2ed-91642d6a0dcc', prompt: 'HR本体提取'  },
];

async function waitExtraction(page, maxMin = 12) {
  for (let i = 0; i < maxMin * 60 / 4; i++) {
    await sleep(4000);
    const pct = await page.locator('p').filter({ hasText: '%' }).first().textContent().catch(() => '');
    process.stdout.write(`\r    进度: ${pct?.trim() ?? '?'}        `);
    if (await page.locator('.bg-green-500').count() > 0) { console.log('\n    ✅ 完成'); return 'ok'; }
    if (await page.locator('text=提取失败').count() > 0) { console.log('\n    ❌ 失败'); return 'fail'; }
  }
  console.log('\n    ⏰ 超时'); return 'timeout';
}

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

  for (const { name, oid, prompt } of TARGETS) {
    console.log(`\n${'═'.repeat(60)}`);
    console.log(`  [${name}]  Prompt: ${prompt}`);
    console.log(`${'═'.repeat(60)}`);

    await page.goto(`${BASE}/ontologies/${oid}`);
    await page.waitForLoadState('networkidle');
    await sleep(600);

    // Click LLM提取配置 tab
    const infoBtn = page.locator('button').filter({ hasText: /LLM提取配置|信息/ }).first();
    await infoBtn.click().catch(() => {});
    await sleep(600);

    // Select the new prompt
    const selects = page.locator('select');
    const promptOpts = await selects.nth(0).locator('option').allTextContents();
    console.log(`  可用 prompts: ${promptOpts.filter(t => t.trim()).join(' | ')}`);
    const target = promptOpts.find(t => t.includes(prompt));
    if (target) {
      await selects.nth(0).selectOption({ label: target });
      console.log(`  ✓ 选择 prompt: ${target.trim()}`);
    } else {
      console.log(`  ⚠ 未找到 "${prompt}"，跳过`);
      continue;
    }
    await sleep(400);

    // Model selection
    const modelOpts = await selects.nth(1).locator('option').allTextContents();
    const deepOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (deepOpt) await selects.nth(1).selectOption({ label: deepOpt });
    else if (modelOpts.length > 1) await selects.nth(1).selectOption({ index: 1 });
    await sleep(400);

    const sc = await page.locator('select').count();
    if (sc >= 3) {
      const mnOpts = await page.locator('select').nth(2).locator('option').allTextContents();
      const mn = mnOpts.find(t => t.toLowerCase().includes('deepseek')) ?? (mnOpts.length > 1 ? mnOpts[1] : null);
      if (mn) await page.locator('select').nth(2).selectOption({ label: mn });
      await sleep(300);
    }

    await page.screenshot({ path: ss(name, 'LLM配置') });

    // Start extraction
    const btn = page.locator('button:has-text("开始提取")');
    if (await btn.isDisabled()) { console.log('  ❌ 按钮禁用'); continue; }
    await btn.click();
    console.log('  ✓ 已触发提取');
    await sleep(1500);
    await page.screenshot({ path: ss(name, '提取中') });

    const result = await waitExtraction(page, 12);
    await page.screenshot({ path: ss(name, '提取完成') });
    if (result !== 'ok') { results.push({ name, status: result }); continue; }

    // Quality badge
    const badge = await page.locator('text=/\d+ 个问题|完美通过|质量通过/').first().textContent().catch(() => '');
    console.log(`  P0: ${badge.trim()}`);

    // API stats
    const entities = await apiGet(`/ontologies/${oid}/entities`, token);
    const logic    = await apiGet(`/ontologies/${oid}/logic`, token);
    const actions  = await apiGet(`/ontologies/${oid}/actions`, token);
    const graph    = await apiGet(`/ontologies/${oid}/graph`, token);

    const withProps = entities.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
    const withCode  = actions.filter(a => a.function_code?.trim().length > 10).length;
    const edgeCount = graph.edges?.length ?? 0;
    const isolated  = (graph.nodes || []).filter(n =>
      !(graph.edges || []).some(e => e.data.source === n.data.id || e.data.target === n.data.id)
    ).length;

    console.log(`  实体: ${entities.length}（有属性: ${withProps}）  逻辑: ${logic.length}  动作: ${actions.length}（有代码: ${withCode}）`);
    console.log(`  图谱: ${graph.nodes?.length ?? 0} 节点 / ${edgeCount} 边 / ${isolated} 孤立`);
    results.push({ name, status: 'ok', entities: entities.length, withProps, logic: logic.length, actions: actions.length, withCode, edges: edgeCount, isolated });

    // Knowledge graph screenshot
    const tabBtn = page.locator('button').filter({ hasText: '知识图谱' });
    if (await tabBtn.count() > 0) {
      await tabBtn.first().click();
      await sleep(3500);
      await page.screenshot({ path: ss(name, '知识图谱') });
    }
  }

  console.log('\n\n══════════ 重新提取结果汇总 ══════════');
  for (const r of results) {
    if (r.status !== 'ok') { console.log(`  ${r.name}: ❌ ${r.status}`); continue; }
    console.log(`  ${r.name}: 实体${r.entities}(属性${r.withProps}) 逻辑${r.logic} 动作${r.actions}(代码${r.withCode}) 边${r.edges} 孤立${r.isolated}`);
  }
  console.log(`\n📸 共 ${step} 张截图 → ${SSDIR}`);

  await browser.close();
})().catch(e => { console.error('❌', e.message, '\n', e.stack); process.exit(1); });
