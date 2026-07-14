/**
 * 复杂多源数据集测试：
 * - 上传 5 个跨领域文件（供应链+生产+采购+物流+质量控制）
 * - 用"供应链本体提取" prompt + DeepSeek 提取
 * - 验证 P0 质量报告
 * - 验证实体/逻辑/动作的内联编辑功能（属性增删、关联增删）
 * - 覆盖知识图谱视图
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8000/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_complex');
const DOCS  = path.join(__dirname, '../test_data/documents');

mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss = label => path.join(SSDIR, `${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g, '_').slice(0,35)}.png`);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(path, token) {
  const res = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  const j = await res.json();
  return j.data ?? j;
}

// ─── 工具：等待提取完成 ───────────────────────────────────────────────────────
async function waitExtraction(page, maxMinutes = 8) {
  const limit = maxMinutes * 60 / 4;
  for (let i = 0; i < limit; i++) {
    await sleep(4000);
    const pct = await page.locator('p').filter({ hasText: '%' }).first().textContent().catch(() => '');
    process.stdout.write(`\r  进度: ${pct?.trim() ?? '?'}        `);
    if (await page.locator('.bg-green-500').count() > 0) { console.log('\n  ✅ 提取成功'); return 'ok'; }
    if (await page.locator('text=提取失败').count() > 0) { console.log('\n  ❌ 提取失败'); return 'fail'; }
  }
  console.log('\n  ⏰ 超时');
  return 'timeout';
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 40 });
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
  // 2. 创建本体（供应链领域）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 2. 创建本体 ===');
  await page.goto(`${BASE}/ontologies`);
  await page.waitForLoadState('networkidle');
  await page.click('button:has-text("创建本体")');
  await sleep(400);
  const runId = Date.now();
  await page.fill('input[placeholder="名称 *"]', `复杂多源测试-${runId}`);
  await page.selectOption('select', '供应链');
  await page.fill('textarea', '跨领域多文件测试：供应链、生产、采购、物流、质量控制五类文件，验证提取质量与内联编辑');
  await page.click('button:has-text("确认")');
  await page.waitForURL(/\/ontologies\/[a-f0-9-]{36}$/, { timeout: 10000 });
  const oid = page.url().split('/').pop();
  console.log(`  Ontology ID: ${oid}`);
  await sleep(500);
  await page.screenshot({ path: ss('创建成功') });

  // ══════════════════════════════════════════════════════════════════════════
  // 3. 上传 5 个跨领域文件
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 3. 上传 5 个文件 ===');
  await page.waitForSelector('input[type="file"]', { timeout: 8000 });
  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.setInputFiles([
    path.join(DOCS, 'supply_chain.md'),           // 供应链总纲
    path.join(DOCS, 'production_management.md'),  // 生产管理
    path.join(DOCS, 'procurement_management.docx'),// 采购流程
    path.join(DOCS, 'logistics_fulfillment.csv'), // 物流履约
    path.join(DOCS, 'quality_control.csv'),       // 质量控制
  ]);
  console.log('  上传中，等待转换...');
  await sleep(6000);
  const rowCount = await page.locator('table tbody tr').count();
  console.log(`  文件列表行数: ${rowCount}`);
  await page.screenshot({ path: ss('五文件上传完成') });

  // ══════════════════════════════════════════════════════════════════════════
  // 4. LLM 配置（供应链 prompt + DeepSeek）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 4. LLM 配置 ===');
  // 点击"LLM提取配置"标签（InfoTab）
  const infoTabBtn = page.locator('button').filter({ hasText: /LLM提取配置|信息/ }).first();
  await infoTabBtn.click().catch(() => {});
  await sleep(500);

  const selects = page.locator('select');
  // Prompt
  const promptOpts = await selects.nth(0).locator('option').allTextContents();
  const supplyOpt = promptOpts.find(t => t.includes('供应链本体提取'));
  if (supplyOpt) {
    await selects.nth(0).selectOption({ label: supplyOpt });
    console.log(`  ✓ Prompt: ${supplyOpt.trim()}`);
  } else {
    console.log(`  ⚠ 未找到供应链提取prompt，可用: ${promptOpts.slice(0,4).join(' | ')}`);
  }
  await sleep(400);

  // 模型配置
  const modelOpts = await selects.nth(1).locator('option').allTextContents();
  const deepOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
  if (deepOpt) {
    await selects.nth(1).selectOption({ label: deepOpt });
    console.log(`  ✓ 模型配置: ${deepOpt.trim()}`);
  } else {
    console.log(`  ⚠ 未找到 DeepSeek 模型，可用: ${modelOpts.slice(0,4).join(' | ')}`);
    if (modelOpts.length > 1) await selects.nth(1).selectOption({ index: 1 });
  }
  await sleep(600);

  // 具体模型
  const sCountNow = await page.locator('select').count();
  if (sCountNow >= 3) {
    const mnOpts = await page.locator('select').nth(2).locator('option').allTextContents();
    const mn = mnOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (mn) {
      await page.locator('select').nth(2).selectOption({ label: mn });
      console.log(`  ✓ 模型名: ${mn.trim()}`);
    } else if (mnOpts.length > 1) {
      await page.locator('select').nth(2).selectOption({ index: 1 });
      console.log(`  ✓ 模型名(fallback): ${mnOpts[1]?.trim()}`);
    }
  }
  await sleep(300);
  await page.screenshot({ path: ss('LLM配置') });

  // ══════════════════════════════════════════════════════════════════════════
  // 5. 开始提取
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 5. 开始提取 ===');
  const btn = page.locator('button:has-text("开始提取")');
  if (await btn.isDisabled()) {
    console.log('  ❌ 按钮禁用，配置不完整');
    await browser.close(); process.exit(1);
  }
  await btn.click();
  console.log('  ✓ 已触发提取');
  await sleep(1500);
  await page.locator('text=提取进度').scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: ss('提取进度_开始') });

  const extractResult = await waitExtraction(page, 10);
  await page.screenshot({ path: ss('提取完成_状态') });
  if (extractResult !== 'ok') { await browser.close(); process.exit(1); }

  // ══════════════════════════════════════════════════════════════════════════
  // 5b. P0 质量报告
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 5b. P0 质量报告 ===');
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(500);
  const reportCard = page.locator('text=P0 输出质量报告');
  if (await reportCard.count() > 0) {
    await reportCard.scrollIntoViewIfNeeded();
    await sleep(300);
    await page.screenshot({ path: ss('P0质量报告') });
    const badge = await page.locator('text=/\\d+ 个问题|完美通过|质量通过/').first().textContent().catch(() => '');
    console.log(`  P0 结果: ${badge.trim()}`);
  } else {
    console.log('  ⚠ P0 报告卡片未显示');
    await page.screenshot({ path: ss('P0报告未显示') });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 6. 实体列表 + 详情 + 内联属性编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 6. 实体 ===');
  await page.click('button:has-text("实体")');
  await sleep(1200);
  await page.screenshot({ path: ss('实体列表') });

  // API 数据概览
  let entities = [], logic = [], actions = [];
  if (token) {
    entities = await apiGet(`/ontologies/${oid}/entities`, token);
    logic    = await apiGet(`/ontologies/${oid}/logic`, token);
    actions  = await apiGet(`/ontologies/${oid}/actions`, token);
    console.log(`  实体: ${entities.length}  逻辑规则: ${logic.length}  动作: ${actions.length}`);

    const withProps = entities.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
    const withLE    = logic.filter(r => (r.linked_entities ?? []).length > 0).length;
    const withCode  = actions.filter(a => a.function_code?.trim().length > 10).length;
    console.log(`  有属性实体: ${withProps}/${entities.length}`);
    console.log(`  有关联实体的逻辑规则: ${withLE}/${logic.length}`);
    console.log(`  有函数代码的动作: ${withCode}/${actions.length}`);

    // Print first few entities
    entities.slice(0, 6).forEach(e => {
      console.log(`  - [${e.type}] ${e.name_cn} | props: ${JSON.stringify(e.properties).slice(0,60)}`);
    });
  }

  // 点入第一个实体
  const entityLinks = page.locator('a[href*="/entities/"]');
  if (await entityLinks.count() > 0) {
    await entityLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('实体详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('实体详情_底部') });

    // 内联编辑属性：点"编辑"，删除第一个属性，添加新属性
    const propEditBtn = page.locator('button').filter({ hasText: '编辑' }).first();
    if (await propEditBtn.count() > 0) {
      await propEditBtn.click();
      await sleep(400);
      await page.screenshot({ path: ss('实体属性编辑模式') });

      // 删除第一行属性（如有）
      const delBtn = page.locator('button').filter({ has: page.locator('svg') }).nth(0);
      const xBtns = page.locator('tbody button');
      if (await xBtns.count() > 0) {
        await xBtns.first().click();
        await sleep(800);
        console.log('  ✓ 删除了一个属性');
        await page.screenshot({ path: ss('实体属性_删除后') });
      }

      // 添加新属性
      const keyInput = page.locator('input[placeholder="属性名"]');
      const valInput = page.locator('input[placeholder="值"]');
      if (await keyInput.count() > 0) {
        await keyInput.fill('测试属性');
        await valInput.fill('测试值_复杂多源');
        await page.locator('button').filter({ hasText: '添加' }).last().click();
        await sleep(800);
        console.log('  ✓ 添加了新属性');
        await page.screenshot({ path: ss('实体属性_添加后') });
      }

      // 完成编辑
      const doneBtn = page.locator('button').filter({ hasText: '完成' }).first();
      if (await doneBtn.count() > 0) await doneBtn.click();
      await sleep(400);
    }

    // 内联编辑关联逻辑规则
    const logicCardBtns = page.locator('button').filter({ hasText: /编辑/ });
    const logicEditBtn = logicCardBtns.nth(1);
    if (await logicEditBtn.count() > 0) {
      await logicEditBtn.click();
      await sleep(400);
      await page.screenshot({ path: ss('实体_逻辑规则编辑') });
      // 完成
      const done2 = page.locator('button').filter({ hasText: '完成' }).first();
      if (await done2.count() > 0) await done2.click();
      await sleep(300);
    }

    await page.goBack(); await sleep(500);
  }

  // 点入第二个实体（验证不同类型）
  if (await entityLinks.count() > 1) {
    await entityLinks.nth(1).click();
    await sleep(1000);
    await page.screenshot({ path: ss('实体详情2') });
    await page.goBack(); await sleep(400);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 7. 逻辑规则列表 + 详情 + 内联关联实体编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 7. 逻辑规则 ===');
  await page.click('button:has-text("逻辑规则")');
  await sleep(1200);
  await page.screenshot({ path: ss('逻辑规则列表') });

  if (token) {
    logic.slice(0, 5).forEach(r => {
      console.log(`  - ${r.name_cn} | 关联实体: [${(r.linked_entities ?? []).join(', ')}]`);
    });
  }

  const logicLinks = page.locator('a[href*="/logic/"]');
  if (await logicLinks.count() > 0) {
    await logicLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('逻辑规则详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('逻辑规则详情_关联区') });

    // 编辑关联实体
    const entEditBtn = page.locator('button').filter({ hasText: '编辑' }).first();
    if (await entEditBtn.count() > 0) {
      await entEditBtn.click();
      await sleep(500);
      await page.screenshot({ path: ss('逻辑规则_关联实体编辑') });

      // 尝试从下拉添加一个实体
      const addSel = page.locator('select').filter({ hasText: /选择添加/ }).first();
      if (await addSel.count() > 0) {
        const opts = await addSel.locator('option').allTextContents();
        if (opts.length > 1) {
          await addSel.selectOption({ index: 1 });
          await sleep(200);
          await page.locator('button').filter({ hasText: '添加' }).last().click();
          await sleep(800);
          console.log(`  ✓ 逻辑规则：添加关联实体 ${opts[1]?.trim()}`);
          await page.screenshot({ path: ss('逻辑规则_实体添加后') });
        }
      }

      // 完成
      const done = page.locator('button').filter({ hasText: '完成' }).first();
      if (await done.count() > 0) await done.click();
      await sleep(400);
    }

    // 编辑关联动作
    const actEditBtns = page.locator('button').filter({ hasText: '编辑' });
    if (await actEditBtns.count() > 1) {
      await actEditBtns.nth(1).click();
      await sleep(500);
      await page.screenshot({ path: ss('逻辑规则_关联动作编辑') });

      // 尝试添加一个动作
      const actSel = page.locator('select').filter({ hasText: /选择添加/ }).first();
      if (await actSel.count() > 0) {
        const opts = await actSel.locator('option').allTextContents();
        if (opts.length > 1) {
          await actSel.selectOption({ index: 1 });
          await sleep(200);
          await page.locator('button').filter({ hasText: '添加' }).last().click();
          await sleep(800);
          console.log(`  ✓ 逻辑规则：添加关联动作 ${opts[1]?.trim()}`);
          await page.screenshot({ path: ss('逻辑规则_动作添加后') });
        }
      }

      const done2 = page.locator('button').filter({ hasText: '完成' }).first();
      if (await done2.count() > 0) await done2.click();
      await sleep(400);
    }

    await page.goBack(); await sleep(500);
  }

  // 第二个逻辑规则
  if (await logicLinks.count() > 1) {
    await logicLinks.nth(1).click();
    await sleep(1000);
    await page.screenshot({ path: ss('逻辑规则详情2') });
    await page.goBack(); await sleep(400);
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 8. 动作列表 + 详情 + 内联关联编辑
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 8. 动作 ===');
  await page.click('button:has-text("动作")');
  await sleep(1200);
  await page.screenshot({ path: ss('动作列表') });

  if (token) {
    actions.slice(0, 5).forEach(a => {
      const code1 = a.function_code?.trim().split('\n')[0] ?? '(无)';
      console.log(`  - ${a.name_cn} | 关联实体: ${(a.linked_entities ?? []).length}个 | 代码: ${code1.slice(0,50)}`);
    });
  }

  const actionLinks = page.locator('a[href*="/actions/"]');
  if (await actionLinks.count() > 0) {
    await actionLinks.first().click();
    await sleep(1000);
    await page.screenshot({ path: ss('动作详情_顶部') });
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await page.screenshot({ path: ss('动作详情_函数代码和关联') });

    // 编辑关联实体（新的独立卡片）
    const entEditBtn = page.locator('button').filter({ hasText: '编辑' }).first();
    if (await entEditBtn.count() > 0) {
      await entEditBtn.click();
      await sleep(500);
      await page.screenshot({ path: ss('动作_关联实体编辑') });

      // 删除第一个关联实体（如有）
      const chipX = page.locator('span button').first();
      if (await chipX.count() > 0) {
        await chipX.click();
        await sleep(800);
        console.log('  ✓ 动作：删除了一个关联实体');
        await page.screenshot({ path: ss('动作_关联实体删除后') });
      }

      const done = page.locator('button').filter({ hasText: '完成' }).first();
      if (await done.count() > 0) await done.click();
      await sleep(400);
    }

    // 编辑关联逻辑规则（第二个卡片的编辑按钮）
    const logicEditBtns = page.locator('button').filter({ hasText: '编辑' });
    if (await logicEditBtns.count() > 1) {
      await logicEditBtns.nth(1).click();
      await sleep(500);
      await page.screenshot({ path: ss('动作_关联逻辑规则编辑') });

      // 尝试添加逻辑规则
      const logicSel = page.locator('select').filter({ hasText: /选择添加/ }).first();
      if (await logicSel.count() > 0) {
        const opts = await logicSel.locator('option').allTextContents();
        if (opts.length > 1) {
          await logicSel.selectOption({ index: 1 });
          await sleep(200);
          await page.locator('button').filter({ hasText: '添加' }).last().click();
          await sleep(800);
          console.log(`  ✓ 动作：添加关联逻辑规则 ${opts[1]?.trim()}`);
          await page.screenshot({ path: ss('动作_逻辑规则添加后') });
        }
      }

      const done2 = page.locator('button').filter({ hasText: '完成' }).first();
      if (await done2.count() > 0) await done2.click();
      await sleep(400);
    }

    await page.goBack(); await sleep(500);
  }

  // 第二、三个动作
  for (let i = 1; i <= 2; i++) {
    if (await actionLinks.count() > i) {
      await actionLinks.nth(i).click();
      await sleep(1000);
      await page.screenshot({ path: ss(`动作详情${i+1}`) });
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await sleep(200);
      await page.screenshot({ path: ss(`动作详情${i+1}_底部`) });
      await page.goBack(); await sleep(400);
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 9. 知识图谱
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 9. 知识图谱 ===');
  await page.click('button:has-text("知识图谱")');
  await sleep(3500);
  await page.screenshot({ path: ss('知识图谱_全局') });

  // 尝试点击图中的节点
  const canvas = page.locator('canvas, .cytoscape-container, [id*="graph"], [id*="cy"]').first();
  if (await canvas.count() > 0) {
    const box = await canvas.boundingBox();
    if (box) {
      // 点击图中心
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await sleep(1000);
      await page.screenshot({ path: ss('知识图谱_节点点击') });
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 10. 验证规则（设置页面）
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 10. 验证规则（Settings）===');
  await page.goto(`${BASE}/settings`);
  await sleep(800);
  await page.screenshot({ path: ss('设置_规则列表') });

  // 本体质量验证规则区
  const validationSection = page.locator('text=本体质量验证规则');
  if (await validationSection.count() > 0) {
    await validationSection.scrollIntoViewIfNeeded();
    await sleep(300);
    await page.screenshot({ path: ss('设置_本体质量验证规则') });
  }

  // ══════════════════════════════════════════════════════════════════════════
  // 11. 回到本体详情：导出
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 11. 导出 ===');
  await page.goto(`${BASE}/ontologies/${oid}`);
  await sleep(800);
  const infoTab2 = page.locator('button').filter({ hasText: /LLM提取配置|信息/ }).first();
  await infoTab2.click().catch(() => {});
  await sleep(400);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(400);
  await page.screenshot({ path: ss('导出区') });

  // ══════════════════════════════════════════════════════════════════════════
  // 12. 总览页
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n=== 12. 总览 ===');
  await page.goto(`${BASE}/overview`);
  await sleep(1000);
  await page.screenshot({ path: ss('总览页') });

  // ══════════════════════════════════════════════════════════════════════════
  // 最终 API 质量汇总
  // ══════════════════════════════════════════════════════════════════════════
  console.log('\n\n══ API 质量汇总 ══');
  if (token) {
    const ents   = await apiGet(`/ontologies/${oid}/entities`, token);
    const rules  = await apiGet(`/ontologies/${oid}/logic`, token);
    const acts   = await apiGet(`/ontologies/${oid}/actions`, token);

    const withProps   = ents.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
    const withLE      = rules.filter(r => (r.linked_entities ?? []).length > 0).length;
    const withActEnts = acts.filter(a => (a.linked_entities ?? []).length > 0).length;
    const withActLog  = acts.filter(a => (a.linked_logic_ids ?? []).length > 0).length;
    const withCode    = acts.filter(a => a.function_code?.trim().length > 10).length;

    console.log(`  实体总数:               ${ents.length}`);
    console.log(`  有属性实体:             ${withProps}/${ents.length}`);
    console.log(`  逻辑规则总数:           ${rules.length}`);
    console.log(`  有关联实体的逻辑规则:   ${withLE}/${rules.length}`);
    console.log(`  动作总数:               ${acts.length}`);
    console.log(`  有关联实体的动作:       ${withActEnts}/${acts.length}`);
    console.log(`  有关联逻辑规则的动作:   ${withActLog}/${acts.length}`);
    console.log(`  有 function_code 的动作: ${withCode}/${acts.length}`);
  }

  console.log(`\n\n✅ 测试完成！共 ${step} 张截图 → ${SSDIR}`);
  await browser.close();
})().catch(e => {
  console.error('\n\n❌ 测试失败:', e.message);
  console.error(e.stack);
  process.exit(1);
});
