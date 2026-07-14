/**
 * 详情页截图：实体、逻辑规则、动作各一个完整页面
 * 复用上次提取的本体 ID
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8002/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_detail');
const OID = 'ef1a1be8-d336-4c82-af43-eddd9fe75019'; // 上次复杂测试的本体

mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss = label => path.join(SSDIR, `${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g, '_').slice(0,40)}.png`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const res = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  const j = await res.json();
  return j.data ?? j;
}

async function scrollShot(page, label) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(200);
  await page.screenshot({ path: ss(label + '_顶部'), fullPage: false });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(200);
  await page.screenshot({ path: ss(label + '_底部'), fullPage: false });
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 40 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  // ─── 登录 ─────────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  await sleep(400);
  const token = await page.evaluate(() => localStorage.getItem('token') || '');
  console.log('  已登录');

  // ─── 拉取数据，挑选展示对象 ───────────────────────────────────────────────
  const entities = await apiGet(`/ontologies/${OID}/entities`, token);
  const rules    = await apiGet(`/ontologies/${OID}/logic`, token);
  const actions  = await apiGet(`/ontologies/${OID}/actions`, token);

  // 挑有属性的实体
  const targetEntity = entities.find(e => Object.keys(e.properties ?? {}).length >= 2)
    ?? entities[0];
  // 挑有关联实体的逻辑规则
  const targetRule = rules.find(r => (r.linked_entities ?? []).length >= 2) ?? rules[0];
  // 挑有 function_code 的动作
  const targetAction = actions.find(a => a.function_code?.trim().length > 10) ?? actions[0];

  console.log(`\n选择的实体: ${targetEntity?.name_cn} [${targetEntity?.type}]`);
  console.log(`选择的逻辑: ${targetRule?.name_cn}`);
  console.log(`选择的动作: ${targetAction?.name_cn}`);

  // ══════════════════════════════════════════════════════════════════════════
  // 1. 实体详情页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 实体详情 ===');
  await page.goto(`${BASE}/ontologies/${OID}/entities/${targetEntity.id}`);
  await sleep(1500);
  await scrollShot(page, `实体_${targetEntity.name_cn}`);

  // 展开属性编辑模式截图
  const propEditBtns = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await propEditBtns.count() > 0) {
    await propEditBtns.first().click();
    await sleep(400);
    await page.screenshot({ path: ss(`实体_属性编辑模式`) });
    // 还原
    const done = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done.count() > 0) await done.first().click();
    await sleep(300);
  }

  // 展开逻辑规则编辑模式截图
  const allEditBtns = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await allEditBtns.count() > 1) {
    await allEditBtns.nth(1).click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`实体_关联逻辑编辑模式`) });
    const done2 = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done2.count() > 0) await done2.first().click();
    await sleep(300);
  }

  // 展开动作编辑模式截图
  const allEditBtns2 = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await allEditBtns2.count() > 2) {
    await allEditBtns2.nth(2).click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`实体_关联动作编辑模式`) });
    const done3 = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done3.count() > 0) await done3.first().click();
    await sleep(300);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 2. 逻辑规则详情页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 逻辑规则详情 ===');
  await page.goto(`${BASE}/ontologies/${OID}/logic/${targetRule.id}`);
  await sleep(1500);
  await scrollShot(page, `逻辑_${targetRule.name_cn}`);

  // 关联实体编辑模式
  const ruleEditBtns = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await ruleEditBtns.count() > 0) {
    await ruleEditBtns.first().click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`逻辑_关联实体编辑模式`) });
    const done = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done.count() > 0) await done.first().click();
    await sleep(300);
  }

  // 关联动作编辑模式
  const ruleEditBtns2 = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await ruleEditBtns2.count() > 1) {
    await ruleEditBtns2.nth(1).click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`逻辑_关联动作编辑模式`) });
    const done2 = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done2.count() > 0) await done2.first().click();
    await sleep(300);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 3. 动作详情页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 动作详情 ===');
  await page.goto(`${BASE}/ontologies/${OID}/actions/${targetAction.id}`);
  await sleep(1500);
  await scrollShot(page, `动作_${targetAction.name_cn}`);

  // 关联实体编辑模式
  const actEditBtns = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await actEditBtns.count() > 0) {
    await actEditBtns.first().click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`动作_关联实体编辑模式`) });
    const done = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done.count() > 0) await done.first().click();
    await sleep(300);
  }

  // 关联逻辑编辑模式
  const actEditBtns2 = page.locator('button').filter({ hasText: /^编辑$/ });
  if (await actEditBtns2.count() > 1) {
    await actEditBtns2.nth(1).click();
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(200);
    await page.screenshot({ path: ss(`动作_关联逻辑编辑模式`) });
    const done2 = page.locator('button').filter({ hasText: /^完成$/ });
    if (await done2.count() > 0) await done2.first().click();
    await sleep(300);
  }

  console.log(`\n✅ 完成，共 ${step} 张截图 → ${SSDIR}`);
  await browser.close();
})().catch(e => {
  console.error('❌ 失败:', e.message);
  process.exit(1);
});
