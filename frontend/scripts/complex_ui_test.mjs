/**
 * 复杂多源数据集 UI 测试（使用已有本体）
 * 本体 ID: ef1a1be8-d336-4c82-af43-eddd9fe75019（复杂多源测试，70实体，5规则，3动作）
 * 跳过提取步骤，直接验证全部 UI 功能
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8000/api/v1';
const OID  = 'ef1a1be8-d336-4c82-af43-eddd9fe75019';
const SSDIR = path.join(__dirname, 'screenshots_complex_ui');
mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss = label => path.join(SSDIR, `${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g, '_').slice(0,35)}.png`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

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

  // ══════════════════════════════════════════════════════════════════════════
  // 1. 登录
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 1. 登录 ===');
  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  await sleep(600);
  token = await page.evaluate(() => localStorage.getItem('token') || '');
  console.log(`  token: ${token ? token.slice(0,20)+'...' : '未获取'}`);
  await page.screenshot({ path: ss('登录成功') });

  // ══════════════════════════════════════════════════════════════════════════
  // 2. API 数据概览
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 2. API 数据概览 ===');
  const entities = await apiGet(`/ontologies/${OID}/entities`, token);
  const logic    = await apiGet(`/ontologies/${OID}/logic`, token);
  const actions  = await apiGet(`/ontologies/${OID}/actions`, token);

  const entArr  = Array.isArray(entities) ? entities : (entities.items ?? []);
  const logArr  = Array.isArray(logic) ? logic : (logic.items ?? []);
  const actArr  = Array.isArray(actions) ? actions : (actions.items ?? []);

  console.log(`  实体: ${entArr.length}  逻辑规则: ${logArr.length}  动作: ${actArr.length}`);
  const withProps = entArr.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
  const withLE    = logArr.filter(r => (r.linked_entities ?? []).length > 0).length;
  const withCode  = actArr.filter(a => a.function_code?.trim().length > 10).length;
  console.log(`  有属性实体: ${withProps}/${entArr.length}`);
  console.log(`  有关联实体的逻辑规则: ${withLE}/${logArr.length}`);
  console.log(`  有函数代码的动作: ${withCode}/${actArr.length}`);
  entArr.slice(0, 5).forEach(e => console.log(`  - [${e.type}] ${e.name_cn ?? e.name_en} | props:${Object.keys(e.properties ?? {}).length}`));

  // ══════════════════════════════════════════════════════════════════════════
  // 3. 导航到本体详情
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 3. 本体详情页 ===');
  await page.goto(`${BASE}/ontologies/${OID}`);
  await page.waitForLoadState('networkidle');
  await sleep(800);
  await page.screenshot({ path: ss('本体详情页') });

  // ══════════════════════════════════════════════════════════════════════════
  // 4. LLM 提取配置 Tab（查看导出按钮）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 4. LLM 提取/导出 Tab ===');
  const infoTabBtn = page.locator('button').filter({ hasText: /LLM|Info|提取配置|基本信息/ }).first();
  await infoTabBtn.click().catch(() => {});
  await sleep(500);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(400);
  await page.screenshot({ path: ss('导出区域') });

  // 检查导出按钮
  const exportLinks = page.locator('a[download]');
  const exportCount = await exportLinks.count();
  console.log(`  导出按钮数量: ${exportCount}`);
  if (exportCount > 0) {
    const formats = await exportLinks.allTextContents();
    console.log(`  导出格式: ${formats.join(', ')}`);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 5. 实体列表
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 5. 实体列表 ===');
  await page.click('button:has-text("实体")');
  await sleep(1200);
  const entityRows = await page.locator('table tbody tr').count();
  console.log(`  实体行数 (当前页): ${entityRows}`);
  await page.screenshot({ path: ss('实体列表') });

  // ══════════════════════════════════════════════════════════════════════════
  // 6. 实体详情 + 内联属性编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 6. 实体详情 ===');
  const entityLinks = page.locator('a[href*="/entities/"]');
  if (await entityLinks.count() > 0) {
    await entityLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('实体详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('实体详情_底部') });

    // 内联编辑属性
    const editBtns = page.locator('button').filter({ hasText: '编辑' });
    if (await editBtns.count() > 0) {
      await editBtns.first().click();
      await sleep(400);
      await page.screenshot({ path: ss('实体属性_编辑模式') });

      // 添加新属性
      const keyInput = page.locator('input[placeholder*="属性名"], input[placeholder*="key"], input[placeholder*="Key"]').first();
      const valInput = page.locator('input[placeholder*="值"], input[placeholder*="value"], input[placeholder*="Value"]').first();
      if (await keyInput.count() > 0) {
        await keyInput.fill('test_key');
        await valInput.fill('test_value_complex');
        const addBtn = page.locator('button').filter({ hasText: /添加|Add/ }).last();
        if (await addBtn.count() > 0) {
          await addBtn.click();
          await sleep(800);
          console.log('  ✓ 添加了属性');
          await page.screenshot({ path: ss('实体属性_添加后') });
        }
      }

      // 完成编辑
      const doneBtn = page.locator('button').filter({ hasText: /完成|Done/ }).first();
      if (await doneBtn.count() > 0) { await doneBtn.click(); await sleep(400); }
    }
    await page.goBack(); await sleep(600);
  }

  // 第二个实体
  if (await entityLinks.count() > 1) {
    await entityLinks.nth(1).click();
    await sleep(1000);
    await page.screenshot({ path: ss('实体详情2') });
    await page.goBack(); await sleep(400);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 7. 逻辑规则列表 + 详情
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 7. 逻辑规则 ===');
  await page.click('button:has-text("逻辑规则")');
  await sleep(1200);
  const logicRows = await page.locator('table tbody tr').count();
  console.log(`  逻辑规则行数: ${logicRows}`);
  await page.screenshot({ path: ss('逻辑规则列表') });

  logArr.slice(0, 5).forEach(r => console.log(`  - ${r.name_cn ?? r.formula?.slice(0,40)} | 关联: [${(r.linked_entities ?? []).join(', ').slice(0,60)}]`));

  const logicLinks = page.locator('a[href*="/logic/"]');
  if (await logicLinks.count() > 0) {
    await logicLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('逻辑规则详情_顶') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('逻辑规则详情_关联') });

    // 编辑关联实体
    const entEdit = page.locator('button').filter({ hasText: '编辑' }).first();
    if (await entEdit.count() > 0) {
      await entEdit.click();
      await sleep(400);
      await page.screenshot({ path: ss('逻辑规则_关联实体编辑') });
      const done = page.locator('button').filter({ hasText: /完成|Done/ }).first();
      if (await done.count() > 0) { await done.click(); await sleep(300); }
    }
    await page.goBack(); await sleep(500);
  }

  if (await logicLinks.count() > 1) {
    await logicLinks.nth(1).click();
    await sleep(1000);
    await page.screenshot({ path: ss('逻辑规则详情2') });
    await page.goBack(); await sleep(400);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 8. 动作列表 + 详情
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 8. 动作 ===');
  await page.click('button:has-text("动作")');
  await sleep(1200);
  const actionRows = await page.locator('table tbody tr').count();
  console.log(`  动作行数: ${actionRows}`);
  await page.screenshot({ path: ss('动作列表') });

  actArr.slice(0, 5).forEach(a => {
    const code1 = a.function_code?.trim().split('\n')[0] ?? '(无)';
    console.log(`  - ${a.name_cn ?? a.execution_rule?.slice(0,30)} | 代码: ${code1.slice(0,50)}`);
  });

  const actionLinks = page.locator('a[href*="/actions/"]');
  if (await actionLinks.count() > 0) {
    await actionLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('动作详情_顶') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('动作详情_代码和关联') });

    // 编辑关联实体
    const entEdit = page.locator('button').filter({ hasText: '编辑' }).first();
    if (await entEdit.count() > 0) {
      await entEdit.click();
      await sleep(400);
      await page.screenshot({ path: ss('动作_关联实体编辑') });
      const done = page.locator('button').filter({ hasText: /完成|Done/ }).first();
      if (await done.count() > 0) { await done.click(); await sleep(300); }
    }
    await page.goBack(); await sleep(500);
  }

  for (let i = 1; i <= 2; i++) {
    if (await actionLinks.count() > i) {
      await actionLinks.nth(i).click();
      await sleep(1000);
      await page.screenshot({ path: ss(`动作详情${i+1}`) });
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await sleep(200);
      await page.screenshot({ path: ss(`动作详情${i+1}_底`) });
      await page.goBack(); await sleep(400);
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 9. 知识图谱
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 9. 知识图谱 ===');
  await page.click('button:has-text("知识图谱")');
  await sleep(4000);
  await page.screenshot({ path: ss('知识图谱_全局') });

  // 点击图中心（尝试触发节点选中）
  const canvas = page.locator('canvas, .cytoscape-container, [id*="graph"], [id*="cy"]').first();
  if (await canvas.count() > 0) {
    const box = await canvas.boundingBox();
    if (box) {
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await sleep(1000);
      await page.screenshot({ path: ss('知识图谱_节点点击') });
    }
  } else {
    // 尝试点击 SVG 中心
    const svg = page.locator('svg').first();
    if (await svg.count() > 0) {
      const box = await svg.boundingBox();
      if (box) {
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        await sleep(800);
        await page.screenshot({ path: ss('知识图谱_SVG点击') });
      }
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 10. 文件 Tab
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 10. 文件列表 ===');
  await page.click('button:has-text("文件")');
  await sleep(1000);
  const fileRows = await page.locator('table tbody tr').count();
  console.log(`  文件列表行数: ${fileRows}`);
  await page.screenshot({ path: ss('文件列表') });

  // ══════════════════════════════════════════════════════════════════════════
  // 11. 设置页面（验证规则）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 11. 设置页（验证规则）===');
  await page.goto(`${BASE}/settings`);
  await sleep(800);
  await page.screenshot({ path: ss('设置页_规则') });

  // 本体质量验证规则区
  const validSec = page.locator('text=本体质量验证规则').first();
  if (await validSec.count() > 0) {
    await validSec.scrollIntoViewIfNeeded();
    await sleep(300);
    await page.screenshot({ path: ss('设置_质量验证规则') });
    console.log('  ✓ 找到质量验证规则区');
  } else {
    console.log('  ⚠ 质量验证规则区未显示');
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 12. 总览页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 12. 总览页 ===');
  await page.goto(`${BASE}/overview`);
  await sleep(1000);
  await page.screenshot({ path: ss('总览页') });

  // 检查统计数字
  const statCards = await page.locator('.font-bold, .text-2xl, .text-3xl').allTextContents();
  console.log(`  统计数字: ${statCards.slice(0,8).join(' | ')}`);

  // ══════════════════════════════════════════════════════════════════════════
  // 13. 本体列表页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 13. 本体列表 ===');
  await page.goto(`${BASE}/ontologies`);
  await sleep(800);
  await page.screenshot({ path: ss('本体列表页') });
  const ontRows = await page.locator('table tbody tr').count();
  console.log(`  本体列表行数: ${ontRows}`);

  // ══════════════════════════════════════════════════════════════════════════
  // 最终 API 质量汇总
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n\n══ API 质量汇总（复杂多源本体）══');
  const ents   = Array.isArray(entities) ? entities : (entities.items ?? []);
  const rules  = Array.isArray(logic) ? logic : (logic.items ?? []);
  const acts   = Array.isArray(actions) ? actions : (actions.items ?? []);

  const withPropsF   = ents.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
  const withLEF      = rules.filter(r => (r.linked_entities ?? []).length > 0).length;
  const withActEnts  = acts.filter(a => (a.linked_entities ?? []).length > 0).length;
  const withActLog   = acts.filter(a => (a.linked_logic_ids ?? []).length > 0).length;
  const withCodeF    = acts.filter(a => a.function_code?.trim().length > 10).length;

  console.log(`  实体总数:                ${ents.length}`);
  console.log(`  有属性实体:              ${withPropsF}/${ents.length}`);
  console.log(`  逻辑规则总数:            ${rules.length}`);
  console.log(`  有关联实体的逻辑规则:    ${withLEF}/${rules.length}`);
  console.log(`  动作总数:                ${acts.length}`);
  console.log(`  有关联实体的动作:        ${withActEnts}/${acts.length}`);
  console.log(`  有关联逻辑规则的动作:    ${withActLog}/${acts.length}`);
  console.log(`  有 function_code 的动作: ${withCodeF}/${acts.length}`);

  console.log(`\n✅ 测试完成！共 ${step} 张截图 → ${SSDIR}`);
  await browser.close();
})().catch(e => {
  console.error('\n❌ 测试失败:', e.message);
  console.error(e.stack);
  process.exit(1);
});
