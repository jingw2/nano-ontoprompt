/**
 * 供应链领域 UI 改进验证截图（聚焦新功能）
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import http from 'http';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const SS   = path.join(__dirname, 'supply_chain_screenshots');
fs.mkdirSync(SS, { recursive: true });

async function getToken() {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ username: 'admin', password: 'admin123' });
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v1/auth/login', method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, res => {
      let data = ''; res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data).data.access_token); } catch { reject(new Error(data)); } });
    });
    req.on('error', reject); req.write(body); req.end();
  });
}

// 获取本体列表（选一个有实体的）
async function getOntologies(token) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v1/ontologies?page_size=50', headers: { 'Authorization': 'Bearer ' + token } }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { reject(new Error(d)); } });
    });
    req.on('error', reject); req.end();
  });
}

let step = 0;
async function shot(page, label) {
  const file = path.join(SS, `${String(step).padStart(2,'0')}_${label}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  📸 ${String(step).padStart(2,'0')}_${label}.png`);
  step++;
  return file;
}
async function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

console.log('═══════════════════════════════════════════════');
console.log('  UI 改进验证截图');
console.log('═══════════════════════════════════════════════');

const token = await getToken();
console.log('  ✅ Token 获取成功');

const ontoData = await getOntologies(token);
const items = ontoData?.data?.items ?? ontoData?.items ?? [];
const supplyOntology = items.find(o => o.domain === '供应链') || items[0];
console.log(`  📋 使用本体: ${supplyOntology?.name} (${supplyOntology?.id?.slice(0,8)})`);

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });

// Zustand persist + API token + language
await ctx.addInitScript(t => {
  localStorage.setItem('auth-store', JSON.stringify({ state: { user: { id: 'a', username: 'admin', role: 'admin' }, token: t }, version: 0 }));
  localStorage.setItem('token', t);
  localStorage.setItem('lang', 'zh');
}, token);

// 拦截 API 直连后端
await ctx.route('http://localhost:5173/api/**', async route => {
  const url = route.request().url().replace('http://localhost:5173', 'http://localhost:8000');
  try { const r = await route.fetch({ url }); await route.fulfill({ response: r }); }
  catch { await route.continue(); }
});

const page = await ctx.newPage();

// ── 1. 首页 Overview
console.log('\n[1] Overview...');
await page.goto(`${BASE}/overview`);
await page.waitForLoadState('networkidle');
await wait(1500);
await shot(page, 'overview_dashboard');

// ── 2. 导航栏（Prompts 子入口）
console.log('[2] 导航栏含 Prompts...');
await shot(page, 'nav_with_prompts_link');

// ── 3. 本体列表（实体/关系数列）
console.log('[3] 本体列表（实体+关系数）...');
await page.goto(`${BASE}/ontologies`);
await page.waitForLoadState('networkidle');
await wait(1500);
await shot(page, 'ontology_list_with_counts');

// ── 4. 新建向导（直接导航）
console.log('[4] 新建本体向导...');
await page.goto(`${BASE}/ontologies/new`);
await page.waitForLoadState('networkidle');
await wait(1500);
await shot(page, 'wizard_page');

// ── 5. 进入供应链本体详情（基本信息 Tab 优先）
if (supplyOntology) {
  console.log('[5] 本体详情 — 基本信息 Tab...');
  await page.goto(`${BASE}/ontologies/${supplyOntology.id}`);
  await page.waitForLoadState('networkidle');
  await wait(1500);
  await shot(page, 'detail_info_tab');

  // ── 6. 实体 Tab（搜索框）
  console.log('[6] 实体 Tab 搜索...');
  const entBtn = page.locator('button:has-text("实体")').first();
  if (await entBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await entBtn.click();
    await wait(1200);
    await shot(page, 'entities_tab_search');

    // 在搜索框输入
    const searchBox = page.locator('input[placeholder*="搜索"]').first();
    if (await searchBox.isVisible({ timeout: 1000 }).catch(() => false)) {
      await searchBox.fill('供应');
      await wait(600);
      await shot(page, 'entities_search_result');
      await searchBox.fill('');
    }
  }

  // ── 7. 知识图谱 Tab（搜索栏）
  console.log('[7] 知识图谱搜索栏...');
  const graphBtn = page.locator('button:has-text("知识图谱")').first();
  if (await graphBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await graphBtn.click();
    await wait(3000);
    await shot(page, 'graph_tab_with_searchbar');

    const graphSearch = page.locator('input[placeholder*="搜索节点"]');
    if (await graphSearch.isVisible({ timeout: 1000 }).catch(() => false)) {
      await graphSearch.fill('供应');
      await wait(600);
      await shot(page, 'graph_search_highlight');
    }
  }

  // ── 8. 逻辑规则 Tab（搜索框）
  console.log('[8] 逻辑规则搜索...');
  const logicBtn = page.locator('button:has-text("逻辑规则")').first();
  if (await logicBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await logicBtn.click();
    await wait(1200);
    await shot(page, 'logic_tab_search');
  }

  // ── 9. 文件上传 Tab（移到末尾）
  console.log('[9] 文件上传 Tab（末尾）...');
  const filesBtn = page.locator('button:has-text("文件上传")').first();
  if (await filesBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await filesBtn.click();
    await wait(1000);
    await shot(page, 'files_tab_last');
  }
}

// ── 10. Prompts 页（通过导航栏）
console.log('[10] Prompts 页...');
await page.goto(`${BASE}/prompts`);
await page.waitForLoadState('networkidle');
await wait(1200);
await shot(page, 'prompts_page');

await browser.close();
console.log(`\n═══════════════════════════════════════════════`);
console.log(`  完成！共 ${step} 张截图 → ${SS}`);
console.log(`═══════════════════════════════════════════════`);
