import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8002/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_entity');
const OID = 'ef1a1be8-d336-4c82-af43-eddd9fe75019';

mkdirSync(SSDIR, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const res = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await res.json()).data ?? (await res.json());
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 40 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('token') || '');

  // 拿一个有属性 + 有关联逻辑的实体
  const res = await fetch(`${API}/ontologies/${OID}/entities`, { headers: { Authorization: `Bearer ${token}` } });
  const entities = (await res.json()).data ?? [];
  const target = entities.find(e => Object.keys(e.properties ?? {}).length >= 2) ?? entities[0];
  console.log('目标实体:', target.name_cn, target.id);

  await page.goto(`${BASE}/ontologies/${OID}/entities/${target.id}`);
  await sleep(2000);

  // 1. 全页截图（view-only）
  await page.screenshot({ path: path.join(SSDIR, '01_实体详情_全页.png'), fullPage: true });
  console.log('✓ 全页截图');

  // 各卡片的编辑按钮：通过卡片标题找，避免误点全局"编辑"
  const cardSections = [
    { title: '实体属性',        file: '02_属性编辑模式_全页.png' },
    { title: '关联实体（图关系）', file: '03_图关系编辑模式_全页.png' },
    { title: '关联逻辑规则',    file: '04_关联逻辑编辑_全页.png' },
    { title: '关联动作',        file: '05_关联动作编辑_全页.png' },
  ];

  for (const { title, file } of cardSections) {
    // 找包含该标题的卡片容器，再找其内的编辑按钮
    const card = page.locator('div.bg-white').filter({ hasText: title }).first();
    const editBtn = card.locator('button').filter({ hasText: /编辑/ }).first();
    if (await editBtn.count() === 0) { console.log(`  ⚠ ${title} 无编辑按钮`); continue; }
    await editBtn.click();
    await sleep(500);
    await page.screenshot({ path: path.join(SSDIR, file), fullPage: true });
    console.log(`  ✓ ${title}`);
    const done = card.locator('button').filter({ hasText: /完成/ }).first();
    if (await done.count() > 0) await done.click();
    await sleep(400);
  }

  console.log('✅ 完成');
  await browser.close();
})().catch(e => { console.error('❌', e.message); process.exit(1); });
