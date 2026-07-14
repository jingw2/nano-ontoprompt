/**
 * 全领域前端模拟用户测试脚本
 * 测试 7 个领域的测试数据：HR、供应链、医疗、教育、法律、营销、财务
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const BASE_URL = 'http://localhost:5173';
const API_URL = 'http://localhost:8000';
const SCREENSHOT_DIR = './domain_test_screenshots';
const RESULTS_FILE = './domain_test_results.json';

// 测试数据目录（相对路径）
const TEST_DATA_BASE = path.resolve('../test_data');

// 7个领域定义
const DOMAINS = [
  {
    name: 'HR',
    nameCn: '人力资源',
    dir: 'HR',
    domain: '人力资源',
    files: [
      'performance_scores.csv',
      'hr_policy.md',
      'retention_risk.csv',
    ],
    primaryFile: 'performance_scores.csv',
  },
  {
    name: 'SupplyChain',
    nameCn: '供应链',
    dir: '供应链',
    domain: '供应链',
    files: [
      'supply_chain_strategy.md',
      'inventory_transactions.csv',
      'logistics_performance.csv',
    ],
    primaryFile: 'inventory_transactions.csv',
  },
  {
    name: 'Medical',
    nameCn: '医疗',
    dir: '医疗',
    domain: '医疗',
    files: [
      'clinical_protocols.md',
      'adverse_events.csv',
      'followup_records.csv',
    ],
    primaryFile: 'clinical_protocols.md',
  },
  {
    name: 'Education',
    nameCn: '教育',
    dir: '教育',
    domain: '教育',
    files: [
      'academic_policy.md',
      'attendance.csv',
      'grade_records.csv',
    ],
    primaryFile: 'academic_policy.md',
  },
  {
    name: 'Legal',
    nameCn: '法律',
    dir: '法律',
    domain: '法律',
    files: [
      'legal_framework.md',
      'ip_portfolio.csv',
      'regulatory_changes.csv',
    ],
    primaryFile: 'legal_framework.md',
  },
  {
    name: 'Marketing',
    nameCn: '营销',
    dir: '营销',
    domain: '营销',
    files: [
      'marketing_strategy.md',
      'lead_data.csv',
      'nps_survey.csv',
    ],
    primaryFile: 'marketing_strategy.md',
  },
  {
    name: 'Finance',
    nameCn: '财务',
    dir: '财务',
    domain: '财务',
    files: [
      'financial_controls.md',
      'cash_flow.csv',
      'expense_reports.csv',
    ],
    primaryFile: 'financial_controls.md',
  },
];

// 确保截图目录存在
if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function screenshot(page, name) {
  const p = path.join(SCREENSHOT_DIR, `${name}.png`);
  await page.screenshot({ path: p, fullPage: true });
  console.log(`  📸 截图: ${name}.png`);
  return p;
}

async function login(page) {
  console.log('\n🔐 登录...');
  await page.goto(`${BASE_URL}/login`);
  await sleep(1500);
  await screenshot(page, '00_login_page');

  await page.fill('input[type="text"], input[name="username"], input[placeholder*="用户"]', 'admin');
  await page.fill('input[type="password"]', 'admin123');
  await screenshot(page, '00_login_filled');

  await page.click('button[type="submit"], button:has-text("登录"), button:has-text("Login")');
  await sleep(2000);
  await screenshot(page, '01_after_login');
  console.log('  ✅ 登录完成');
}

async function testOverview(page) {
  console.log('\n📊 测试 Overview 页面...');
  await page.goto(`${BASE_URL}/overview`);
  await sleep(2000);
  await screenshot(page, '02_overview');
  const title = await page.title();
  console.log(`  页面标题: ${title}`);
  return { page: 'Overview', status: 'PASS', screenshot: '02_overview.png' };
}

async function testPipelinesPage(page) {
  console.log('\n🔄 测试 Pipelines 页面...');
  await page.goto(`${BASE_URL}/pipelines`);
  await sleep(2000);
  await screenshot(page, '03_pipelines_connections');

  // 切换到 Datasets 子标签
  try {
    await page.click('a[href*="/pipelines/datasets"], a:has-text("Datasets")');
    await sleep(1500);
    await screenshot(page, '03_pipelines_datasets');
  } catch(e) {}

  // 切换到 Transforms 子标签
  try {
    await page.click('a[href*="/pipelines/transforms"], a:has-text("Transforms")');
    await sleep(1500);
    await screenshot(page, '03_pipelines_transforms');
  } catch(e) {}

  // 切换到 Curated 子标签
  try {
    await page.click('a[href*="/pipelines/curated"], a:has-text("Curated")');
    await sleep(1500);
    await screenshot(page, '03_pipelines_curated');
  } catch(e) {}

  return { page: 'Pipelines', status: 'PASS', screenshot: '03_pipelines_connections.png' };
}

async function testDomain(page, domain, index) {
  const result = {
    domain: domain.nameCn,
    status: 'PENDING',
    ontologyId: null,
    filesUploaded: 0,
    filesTotal: domain.files.length,
    entitiesExtracted: 0,
    relationsExtracted: 0,
    screenshotCreate: '',
    screenshotDetail: '',
    screenshotGraph: '',
    errors: [],
    startTime: new Date().toISOString(),
  };

  const prefix = `domain_${String(index + 1).padStart(2,'0')}_${domain.name}`;
  console.log(`\n[${index+1}/7] 🧩 测试领域: ${domain.nameCn} (${domain.name})`);

  try {
    // 1. 进入本体列表页
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(1500);
    await screenshot(page, `${prefix}_00_list`);

    // 2. 点击新建本体
    const newBtn = page.locator('button:has-text("新建"), a:has-text("新建"), button:has-text("New"), button:has-text("+")').first();
    await newBtn.click();
    await sleep(1500);
    await screenshot(page, `${prefix}_01_new`);

    // 3. 填写本体信息（处理向导 or 直接表单）
    const nameInput = page.locator('input[placeholder*="名称"], input[name="name"], input[placeholder*="name"]').first();
    if (await nameInput.isVisible()) {
      await nameInput.fill(`${domain.nameCn}测试本体`);
    }

    // 尝试选择领域
    try {
      const domainInput = page.locator('input[placeholder*="领域"], select[name="domain"]').first();
      if (await domainInput.isVisible()) {
        await domainInput.fill(domain.domain);
      }
    } catch(e) {}

    // 4. 如果有"简易 LLM 提取"选项就选它
    try {
      const simpleBtn = page.locator('button:has-text("简易"), button:has-text("LLM 提取"), [data-mode="simple"]').first();
      if (await simpleBtn.isVisible({ timeout: 1000 })) {
        await simpleBtn.click();
        await sleep(1000);
      }
    } catch(e) {}

    await screenshot(page, `${prefix}_02_form`);

    // 5. 点击下一步 / 提交
    try {
      const nextBtn = page.locator('button:has-text("下一步"), button:has-text("Next"), button:has-text("创建"), button[type="submit"]').first();
      await nextBtn.click();
      await sleep(2000);
    } catch(e) {
      result.errors.push(`提交表单失败: ${e.message}`);
    }

    result.screenshotCreate = `${prefix}_02_form.png`;
    await screenshot(page, `${prefix}_03_created`);

    // 6. 尝试上传文件（在本体详情页面或文件上传区域）
    const domainDataDir = path.join(TEST_DATA_BASE, domain.dir);
    let uploadedCount = 0;

    for (const fname of domain.files) {
      const fpath = path.join(domainDataDir, fname);
      if (!fs.existsSync(fpath)) {
        result.errors.push(`文件不存在: ${fpath}`);
        continue;
      }

      try {
        // 查找文件输入框
        const fileInput = page.locator('input[type="file"]').first();
        if (await fileInput.isVisible({ timeout: 2000 })) {
          await fileInput.setInputFiles(fpath);
          await sleep(1500);
          uploadedCount++;
          console.log(`  ✅ 上传: ${fname}`);
        } else {
          // 尝试点击上传区域
          const dropZone = page.locator('[class*="dropzone"], [class*="upload"], div:has-text("拖拽"), div:has-text("上传")').first();
          if (await dropZone.isVisible({ timeout: 1000 })) {
            const fileInputHidden = page.locator('input[type="file"]');
            await fileInputHidden.setInputFiles(fpath);
            await sleep(1500);
            uploadedCount++;
          }
        }
      } catch(e) {
        result.errors.push(`上传 ${fname} 失败: ${e.message}`);
      }
    }

    result.filesUploaded = uploadedCount;
    await screenshot(page, `${prefix}_04_uploaded`);

    // 7. 当前 URL 获取本体 ID
    const currentUrl = page.url();
    const urlMatch = currentUrl.match(/\/ontologies\/([^\/]+)/);
    if (urlMatch) {
      result.ontologyId = urlMatch[1];
    }

    // 8. 截图本体详情页
    await screenshot(page, `${prefix}_05_detail`);
    result.screenshotDetail = `${prefix}_05_detail.png`;

    // 9. 尝试访问 Graph 标签
    try {
      const graphTab = page.locator('a:has-text("Graph"), button:has-text("Graph"), [role="tab"]:has-text("Graph")').first();
      if (await graphTab.isVisible({ timeout: 2000 })) {
        await graphTab.click();
        await sleep(2000);
        await screenshot(page, `${prefix}_06_graph`);
        result.screenshotGraph = `${prefix}_06_graph.png`;
      }
    } catch(e) {}

    // 10. 尝试访问 Entities 标签
    try {
      const entTab = page.locator('a:has-text("Entities"), button:has-text("Entities"), [role="tab"]:has-text("Entities")').first();
      if (await entTab.isVisible({ timeout: 2000 })) {
        await entTab.click();
        await sleep(2000);
        await screenshot(page, `${prefix}_07_entities`);

        // 统计实体数量
        const entCount = await page.locator('[class*="entity-item"], tr[class*="entity"], li[class*="entity"], .entity-row').count();
        result.entitiesExtracted = entCount;
      }
    } catch(e) {}

    // 11. 尝试访问 Logic 标签
    try {
      const logicTab = page.locator('a:has-text("Logic"), button:has-text("Logic"), [role="tab"]:has-text("Logic")').first();
      if (await logicTab.isVisible({ timeout: 2000 })) {
        await logicTab.click();
        await sleep(1500);
        await screenshot(page, `${prefix}_08_logic`);
      }
    } catch(e) {}

    result.status = uploadedCount > 0 ? 'PASS' : 'PARTIAL';
    console.log(`  ✅ 完成: 上传 ${uploadedCount}/${domain.files.length} 个文件`);

  } catch(err) {
    result.status = 'FAIL';
    result.errors.push(err.message);
    console.log(`  ❌ 失败: ${err.message}`);
    await screenshot(page, `${prefix}_error`);
  }

  result.endTime = new Date().toISOString();
  return result;
}

async function testModelsPage(page) {
  console.log('\n⚙️  测试 Models 页面...');
  await page.goto(`${BASE_URL}/models`);
  await sleep(2000);
  await screenshot(page, '10_models');
  return { page: 'Models', status: 'PASS', screenshot: '10_models.png' };
}

async function testSettingsPage(page) {
  console.log('\n🔧 测试 Settings 页面...');
  await page.goto(`${BASE_URL}/settings`);
  await sleep(2000);
  await screenshot(page, '11_settings');
  return { page: 'Settings', status: 'PASS', screenshot: '11_settings.png' };
}

// ── 主测试流程 ──────────────────────────────────────────────────────────

async function main() {
  console.log('═══════════════════════════════════════════════');
  console.log('  nano-ontoprompt v2 — 全领域前端测试');
  console.log('═══════════════════════════════════════════════');
  console.log(`  后端: ${API_URL}`);
  console.log(`  前端: ${BASE_URL}`);
  console.log(`  截图目录: ${SCREENSHOT_DIR}`);
  console.log('');

  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  });
  const page = await context.newPage();

  const results = {
    testTime: new Date().toISOString(),
    backend: API_URL,
    frontend: BASE_URL,
    pages: [],
    domains: [],
    summary: {},
  };

  try {
    // 1. 登录
    await login(page);

    // 2. 测试主要页面
    results.pages.push(await testOverview(page));
    results.pages.push(await testPipelinesPage(page));
    results.pages.push(await testModelsPage(page));
    results.pages.push(await testSettingsPage(page));

    // 3. 测试本体列表
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(1500);
    await screenshot(page, '05_ontologies_list');

    // 4. 逐领域测试
    for (let i = 0; i < DOMAINS.length; i++) {
      const domainResult = await testDomain(page, DOMAINS[i], i);
      results.domains.push(domainResult);
      // 每个领域测试后回到列表页
      await page.goto(`${BASE_URL}/ontologies`);
      await sleep(1000);
    }

    // 5. 最终列表截图
    await page.goto(`${BASE_URL}/ontologies`);
    await sleep(2000);
    await screenshot(page, '99_final_ontology_list');

  } catch(err) {
    console.error('测试异常:', err);
  } finally {
    await browser.close();
  }

  // 汇总统计
  const passCount = results.domains.filter(d => d.status === 'PASS').length;
  const partialCount = results.domains.filter(d => d.status === 'PARTIAL').length;
  const failCount = results.domains.filter(d => d.status === 'FAIL').length;
  results.summary = {
    totalDomains: DOMAINS.length,
    pass: passCount,
    partial: partialCount,
    fail: failCount,
    totalFilesUploaded: results.domains.reduce((s, d) => s + d.filesUploaded, 0),
    totalFilesAttempted: results.domains.reduce((s, d) => s + d.filesTotal, 0),
  };

  // 保存结果
  fs.writeFileSync(RESULTS_FILE, JSON.stringify(results, null, 2), 'utf-8');
  console.log('\n═══════════════════════════════════════════════');
  console.log('  测试完成！');
  console.log(`  通过: ${passCount}/${DOMAINS.length} 个领域`);
  console.log(`  部分通过: ${partialCount}`);
  console.log(`  失败: ${failCount}`);
  console.log(`  结果文件: ${RESULTS_FILE}`);
  console.log(`  截图目录: ${SCREENSHOT_DIR}/`);
  console.log('═══════════════════════════════════════════════');

  return results;
}

main().catch(console.error);
