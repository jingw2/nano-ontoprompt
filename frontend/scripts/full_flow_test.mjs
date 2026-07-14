/**
 * Full end-to-end frontend test for OntoPrompt.
 * Tests every major feature:
 *   1. Login
 *   2. Ontology list
 *   3. Create new Ontology
 *   4. Step 1: File upload (dropzone + file list)
 *   5. Step 2: LLM config + start extraction
 *   6. Extraction progress (stages + bar)
 *   7. Step 3: Export section
 *   8. Entity / Logic / Action detail pages (from existing ontology)
 *   9. Incremental file upload on existing ontology
 *  10. Settings → 规则列表 tab (toggles + thresholds)
 *  11. Constraint badge in LLM config panel
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE = 'http://localhost:5173';
const SSDIR = path.join(__dirname, 'screenshots');
let step = 0;
const SS = () => path.join(SSDIR, `full_${String(++step).padStart(2,'0')}.png`);
const DOCS = path.join(__dirname, '../test_data/documents');
const RUN_ID = Date.now();

// The ontology that already has extraction results (supply chain 7-file run)
const EXISTING_OID = '73c27076-9210-4b67-8c36-37becbcbd66a';

async function shot(page, label) {
  await page.screenshot({ path: SS(), fullPage: false });
  console.log(`  [${step}] ${label}`);
}
async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 55 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  // ─── 1. Login ───────────────────────────────────────────────────────────────
  console.log('\n=== 1. 登录 ===');
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState('networkidle');
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await shot(page, '登录页 - 填写用户名和密码');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  await sleep(600);
  await shot(page, '登录成功 - 概览仪表盘');

  // ─── 2. Ontology 列表 ───────────────────────────────────────────────────────
  console.log('\n=== 2. Ontology 列表 ===');
  await page.goto(`${BASE}/ontologies`);
  await page.waitForLoadState('networkidle');
  await sleep(500);
  await shot(page, 'Ontology 列表页 - 展示所有 Ontology');

  // ─── 3. 创建新 Ontology ─────────────────────────────────────────────────────
  console.log('\n=== 3. 创建新 Ontology ===');
  await page.click('button:has-text("创建 Ontology")');
  await sleep(600);
  // The dialog uses uncontrolled inputs with placeholders
  await page.fill('input[placeholder="名称 *"]', `全流程UI测试-${RUN_ID}`);
  await page.selectOption('select', '供应链');
  await page.fill('textarea', '全流程测试用本体，验证文件上传→提取→导出工作流');
  await shot(page, `创建弹窗 - 填写名称"全流程UI测试-${RUN_ID}"`);
  await page.click('button:has-text("确认")');
  await page.waitForURL(/\/ontologies\/[a-f0-9-]{36}$/, { timeout: 10000 });
  await sleep(800);
  await shot(page, '创建成功 - 跳转到 Ontology 详情页（新工作流布局）');

  // ─── 4. Step 1 – 文件管理 ────────────────────────────────────────────────────
  console.log('\n=== 4. Step 1: 文件管理 ===');
  await page.waitForSelector('input[type="file"]', { timeout: 8000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await sleep(300);
  await shot(page, 'Step 1: 文件上传 Tab - Dropzone + 空文件列表');

  // Upload 3 files
  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.setInputFiles([
    path.join(DOCS, 'supply_chain.md'),
    path.join(DOCS, 'supplier_list.csv'),
    path.join(DOCS, 'procurement_management.docx'),
  ]);
  console.log('  上传 3 个文件中...');
  await sleep(5000);
  const fileRows = await page.locator('table tbody tr').count();
  console.log(`  文件列表显示 ${fileRows} 行`);
  await shot(page, `Step 1: ${fileRows} 个文件上传完成 - 文件列表展示`);

  // ─── 5. Step 2 – LLM 提取配置 ────────────────────────────────────────────────
  console.log('\n=== 5. Step 2: LLM 提取配置 ===');
  await page.click('button:has-text("LLM提取配置")');
  await sleep(600);
  await shot(page, 'Step 2: LLM 提取配置区域（Prompt + 模型下拉框）');

  // Select prompt (1st select on page)
  const selects = page.locator('select');
  const sCount = await selects.count();
  console.log(`  页面上共有 ${sCount} 个 select`);

  if (sCount >= 1) {
    const promptOpts = await selects.nth(0).locator('option').allTextContents();
    const supplyChainOpt = promptOpts.find(t => t.includes('供应链本体提取'));
    if (supplyChainOpt) {
      await selects.nth(0).selectOption({ label: supplyChainOpt });
      console.log(`  选择 Prompt: ${supplyChainOpt.trim()}`);
    } else if (promptOpts.length > 1) {
      await selects.nth(0).selectOption({ index: 1 });
      console.log(`  选择 Prompt: ${promptOpts[1]?.trim()}`);
    }
    await sleep(400);
  }

  if (sCount >= 2) {
    // Prefer DeepSeek model; fall back to first available
    const modelSelect = selects.nth(1);
    const modelOpts = await modelSelect.locator('option').allTextContents();
    const deepseekOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (deepseekOpt) {
      await modelSelect.selectOption({ label: deepseekOpt });
      console.log(`  选择模型配置: ${deepseekOpt.trim()}`);
    } else if (modelOpts.length > 1) {
      await modelSelect.selectOption({ index: 1 });
      console.log(`  选择模型配置: ${modelOpts[1]?.trim()}`);
    }
    await sleep(600);
  }

  // After picking model, a 3rd select (model name) may appear — pick deepseek-chat if available
  const sCountNow = await page.locator('select').count();
  if (sCountNow >= 3) {
    const mnSelect = page.locator('select').nth(2);
    const mnOpts = await mnSelect.locator('option').allTextContents();
    const deepseekModel = mnOpts.find(t => t.toLowerCase().includes('deepseek'));
    if (deepseekModel) {
      await mnSelect.selectOption({ label: deepseekModel });
      console.log(`  选择具体模型: ${deepseekModel.trim()}`);
    } else if (mnOpts.length > 1) {
      await mnSelect.selectOption({ index: 1 });
      console.log(`  选择具体模型: ${mnOpts[1]?.trim()}`);
    }
    await sleep(300);
  }
  await shot(page, 'Step 2: LLM 配置选择完毕 - 提取按钮可用');

  // ─── 6. 开始提取 + 进度展示 ──────────────────────────────────────────────────
  console.log('\n=== 6. 开始提取（含进度展示）===');
  const extractBtn = page.locator('button:has-text("开始提取")');
  const btnDisabled = await extractBtn.isDisabled();
  console.log(`  开始提取按钮: ${btnDisabled ? '禁用' : '可用'}`);

  if (!btnDisabled) {
    await extractBtn.click();
    console.log('  ✓ 提取已触发');
    await sleep(1500);
    // Scroll down to see progress panel
    await page.locator('text=提取进度').scrollIntoViewIfNeeded().catch(() => {});
    await sleep(400);
    await shot(page, '提取进度面板 - 阶段步骤指示器 + 进度条 (0%)');

    // Poll for progress updates
    let lastPct = '';
    for (let i = 0; i < 45; i++) {
      await sleep(4000);
      const pctEl = page.locator('p:has-text("%")').first();
      const pct = await pctEl.textContent().catch(() => '0%');
      if (pct !== lastPct) {
        process.stdout.write(`\r  进度: ${pct.trim()}        `);
        lastPct = pct;
      }

      // Take screenshot at LLM stage
      if (pct.includes('40') && i < 20) {
        await shot(page, '提取进度 - 40% 正在调用 LLM');
      }

      const success = await page.locator('.bg-green-500').count();
      const failed = await page.locator('text=提取失败').count();
      if (success > 0) { console.log('\n  ✅ 提取成功'); break; }
      if (failed > 0)  { console.log('\n  ❌ 提取失败（可能是网络或配置问题）'); break; }
    }
    await page.locator('text=提取进度').scrollIntoViewIfNeeded().catch(() => {});
    await sleep(300);
    await shot(page, '提取结束 - 进度条显示最终状态');
  } else {
    await shot(page, '⚠️ 提取按钮禁用（检查模型/Prompt 配置）');
  }

  // ─── 7. Step 3 – 导出 ────────────────────────────────────────────────────────
  console.log('\n\n=== 7. Step 3: 导出区域 ===');
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await sleep(400);
  await shot(page, 'Step 3: 导出区域 - JSON / YAML / CSV / TTL / HTML');

  // ─── 8. 用已有 Ontology 测试详情页 ───────────────────────────────────────────
  console.log('\n=== 8. 使用已有提取数据的 Ontology 测试详情页 ===');
  await page.goto(`${BASE}/ontologies/${EXISTING_OID}`);
  await page.waitForLoadState('networkidle');
  await sleep(1000);
  await shot(page, '已有提取结果的 Ontology 详情页');

  // ─── 9. 实体详情页 ───────────────────────────────────────────────────────────
  console.log('\n=== 9. 实体详情页 ===');
  await page.click('button:has-text("实体")');
  await sleep(1200);
  await shot(page, '实体列表 Tab - 提取出的所有实体');

  const entityLink = page.locator('a[href*="/entities/"]').first();
  if (await entityLink.count() > 0) {
    await entityLink.click();
    await sleep(1000);
    await shot(page, '实体详情页 - 基本信息 + 属性');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await shot(page, '实体详情页 - 关联逻辑规则 + 关联动作卡片');
    await page.goBack();
    await sleep(500);
  }

  // ─── 10. 逻辑规则详情页 ──────────────────────────────────────────────────────
  console.log('\n=== 10. 逻辑规则详情页（含新增关联实体卡片）===');
  await page.click('button:has-text("逻辑规则")');
  await sleep(1200);
  await shot(page, '逻辑规则列表 Tab');

  const logicLink = page.locator('a[href*="/logic/"]').first();
  if (await logicLink.count() > 0) {
    await logicLink.click();
    await sleep(1000);
    await shot(page, '逻辑规则详情页 - 规则信息 + 置信度');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await shot(page, '逻辑规则详情页 - 关联实体（蓝色）+ 关联动作（紫色）- 新增卡片');
    await page.goBack();
    await sleep(500);
  }

  // ─── 11. 动作详情页 ──────────────────────────────────────────────────────────
  console.log('\n=== 11. 动作详情页 ===');
  await page.click('button:has-text("动作")');
  await sleep(1200);
  await shot(page, '动作列表 Tab');

  const actionLink = page.locator('a[href*="/actions/"]').first();
  if (await actionLink.count() > 0) {
    await actionLink.click();
    await sleep(1000);
    await shot(page, '动作详情页 - 执行规则信息');
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(300);
    await shot(page, '动作详情页 - 关联实体 + 关联逻辑规则（fix_links.py 填充）');
    await page.goBack();
    await sleep(500);
  }

  // ─── 12. 知识图谱 ─────────────────────────────────────────────────────────────
  console.log('\n=== 12. 知识图谱 ===');
  await page.click('button:has-text("知识图谱")');
  await sleep(3000);
  await shot(page, '知识图谱 - 力导向布局（颜色区分实体类型 + 布局按钮）');

  const hierBtn = page.locator('button:has-text("层级")');
  if (await hierBtn.count() > 0) {
    await hierBtn.click();
    await sleep(1500);
    await shot(page, '知识图谱 - 层级布局');
  }

  // ─── 13. 增量文件上传 ─────────────────────────────────────────────────────────
  console.log('\n=== 13. 增量文件上传（已创建 Ontology）===');
  await page.click('button:has-text("文件上传")');
  await sleep(800);
  await page.evaluate(() => window.scrollTo(0, 0));
  const rowsBefore = await page.locator('table tbody tr').count();
  await shot(page, `增量上传前 - 当前 ${rowsBefore} 个文件（Dropzone 提示"拖拽或点击补充文件"）`);

  const fileInput2 = page.locator('input[type="file"]').first();
  await fileInput2.setInputFiles([path.join(DOCS, 'quality_control.csv')]);
  console.log('  补充上传 quality_control.csv...');
  await sleep(3000);
  const rowsAfter = await page.locator('table tbody tr').count();
  console.log(`  上传后: ${rowsAfter} 个文件`);
  await shot(page, `增量上传后 - 现在 ${rowsAfter} 个文件（可继续提取或保持现有结果）`);

  // ─── 14. 设置 - 规则列表 ─────────────────────────────────────────────────────
  console.log('\n=== 14. 设置 → 规则列表 Tab ===');
  await page.goto(`${BASE}/settings`);
  await page.waitForLoadState('networkidle');
  await sleep(400);
  await shot(page, '设置页 - 默认显示"置信度规则" Tab');

  await page.click('button:has-text("规则列表")');
  await sleep(400);
  await shot(page, '规则列表 Tab - 6 条提取约束规则（全部默认关闭）');

  // Toggle rule 1: 多文档实体验证
  const toggleBtns = page.locator('button[class*="inline-flex"][class*="rounded-full"]');
  const tc = await toggleBtns.count();
  console.log(`  找到 ${tc} 个规则开关`);

  if (tc > 0) {
    await toggleBtns.nth(0).click();
    await sleep(400);
    await shot(page, '开启"多文档实体验证" - 出现"最少文档数"输入框（默认 2）');

    // Adjust threshold
    const numInputs = page.locator('input[type="number"]');
    if (await numInputs.count() > 0) {
      await numInputs.first().triple_click?.() || await numInputs.first().click({ clickCount: 3 });
      await numInputs.first().fill('3');
      await sleep(200);
      await shot(page, '修改"最少文档数"阈值为 3');
    }

    // Toggle rule 2: 多文档规则验证
    if (tc > 1) {
      await toggleBtns.nth(1).click();
      await sleep(300);
    }
    await shot(page, '两条规则开启状态 - 规则立即保存到 localStorage');
  }

  // ─── 15. 验证 Badge 显示在 LLM 配置面板 ─────────────────────────────────────
  console.log('\n=== 15. 验证"规则约束生效" Badge ===');
  await page.goto(`${BASE}/ontologies/${EXISTING_OID}`);
  await page.waitForLoadState('networkidle');
  await sleep(800);
  await page.click('button:has-text("LLM提取配置")');
  await sleep(400);
  await shot(page, '返回 Ontology 详情 - "N条规则约束生效" Badge 显示在 LLM 配置区域');

  // Final full scroll to show whole LLM提取配置 tab
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, '完整 LLM提取配置 Tab 工作流视图');

  console.log(`\n\n✅ 全流程测试完成！共 ${step} 张截图已保存至 screenshots/ 目录`);
  await browser.close();
})().catch(e => {
  console.error('\n\n❌ 测试失败:', e.message);
  process.exit(1);
});
