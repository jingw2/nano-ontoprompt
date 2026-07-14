/**
 * HR & 法律 v2 前端测试
 * 验证 v2 测试数据提取后在 UI 上的完整性：
 *   - 实体列表（属性覆盖率）
 *   - 逻辑规则（含联实体）
 *   - 动作（含 function_code）
 *   - 实体关系图
 *   - 知识图谱
 *   - 总览统计
 */
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE  = 'http://localhost:5173';
const API   = 'http://localhost:8000/api/v1';
const SSDIR = path.join(__dirname, 'screenshots_hr_legal_v2');
mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss   = label => path.join(SSDIR, `${String(++step).padStart(2,'0')}_${label.replace(/[^\w一-龥]/g,'_').slice(0,35)}.png`);
const wait = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(path_, tok) {
  const r = await fetch(`${API}${path_}`, { headers: { Authorization: `Bearer ${tok}` } });
  const j = await r.json();
  return j.data ?? j;
}

async function login(page) {
  await page.goto(`${BASE}/login`);
  await page.fill('input[type="text"], input[name="username"]', 'admin');
  await page.fill('input[type="password"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(ontologies|dashboard|overview)/, { timeout: 10000 });
  console.log('  ✓ 登录成功');
}

async function getToken() {
  const r = await fetch(`${API}/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: 'admin123' }),
  });
  const j = await r.json();
  return j.data?.access_token ?? '';
}

async function findOntology(tok, nameFragment) {
  const data = await apiGet('/ontologies?page=1&page_size=50', tok);
  const items = data.items ?? data;
  return items.find(o => o.name.includes(nameFragment));
}

async function clickTab(page, tabKey) {
  // Try ?tab= URL param first (fastest), then fall back to clicking button
  const url = page.url();
  const base = url.split('?')[0];
  await page.goto(`${base}?tab=${tabKey}`);
  await wait(1800);
}

async function testOntologyDetail(page, tok, oid, label) {
  console.log(`\n${'─'.repeat(55)}`);
  console.log(`  [${label}] oid=${oid}`);
  console.log('─'.repeat(55));

  // ── 导航到详情页 ─────────────────────────────────────────────────────
  await page.goto(`${BASE}/ontologies/${oid}`);
  await wait(2000);
  await page.screenshot({ path: ss(`${label}_detail`) });

  // ── API 统计（正确解析列表格式）──────────────────────────────────────
  const [entArr, ruleArr, actArr, fileArr] = await Promise.all([
    apiGet(`/ontologies/${oid}/entities`, tok).then(d => Array.isArray(d) ? d : (d.items ?? [])),
    apiGet(`/ontologies/${oid}/logic`,    tok).then(d => Array.isArray(d) ? d : (d.items ?? [])),
    apiGet(`/ontologies/${oid}/actions`,  tok).then(d => Array.isArray(d) ? d : (d.items ?? [])),
    apiGet(`/ontologies/${oid}/files`,    tok).then(d => Array.isArray(d) ? d : (d.items ?? [])),
  ]);
  const withProps = entArr.filter(e => Object.keys(e.properties ?? {}).length > 0).length;
  const withLE    = ruleArr.filter(r => (r.linked_entities ?? []).length > 0).length;
  const withCode  = actArr.filter(a => (a.function_code ?? '').trim().length > 20).length;
  console.log(`  API → 实体:${entArr.length}(属性${withProps})  规则:${ruleArr.length}(联实体${withLE})  动作:${actArr.length}(代码${withCode})  文件:${fileArr.length}`);

  // ── 实体 Tab ─────────────────────────────────────────────────────────
  await clickTab(page, 'entities');
  await page.screenshot({ path: ss(`${label}_entities`) });
  const entityRows = await page.locator('table tbody tr').count();
  console.log(`  实体表格行数: ${entityRows}`);

  // 点第一行实体链接看详情
  const entLink = page.locator('a[href*="/entities/"]').first();
  if (await entLink.count()) {
    await entLink.click();
    await wait(1200);
    await page.screenshot({ path: ss(`${label}_entity_detail`) });
    console.log(`  实体详情: ✓`);
    await page.goBack(); await wait(800);
  }

  // ── 逻辑规则 Tab ─────────────────────────────────────────────────────
  await clickTab(page, 'logic');
  await page.screenshot({ path: ss(`${label}_logic`) });
  const ruleRows = await page.locator('table tbody tr').count();
  console.log(`  逻辑规则行数: ${ruleRows}`);

  // ── 动作 Tab ─────────────────────────────────────────────────────────
  await clickTab(page, 'actions');
  await page.screenshot({ path: ss(`${label}_actions`) });
  const actionRows = await page.locator('table tbody tr').count();
  console.log(`  动作行数: ${actionRows}`);

  // 点第一行动作看 function_code
  const actLink = page.locator('a[href*="/actions/"]').first();
  if (await actLink.count()) {
    await actLink.click();
    await wait(1200);
    const hasCode = await page.locator('pre, code, [class*="code"]').count() > 0;
    console.log(`  function_code 可见: ${hasCode ? '✓' : '✗'}`);
    await page.screenshot({ path: ss(`${label}_action_detail`) });
    await page.goBack(); await wait(800);
  }

  // ── 知识图谱 Tab ─────────────────────────────────────────────────────
  await clickTab(page, 'graph');
  await wait(2000); // graph renders async
  await page.screenshot({ path: ss(`${label}_graph`) });
  const hasNodes = await page.locator('canvas, svg circle, svg .node, [class*="node"]').count() > 0;
  console.log(`  知识图谱: ${hasNodes ? '✓ 渲染成功' : '✗ 未渲染'}`);

  // ── 文件 Tab ─────────────────────────────────────────────────────────
  await clickTab(page, 'files');
  await page.screenshot({ path: ss(`${label}_files`) });
  const fileRows = await page.locator('table tbody tr').count();
  console.log(`  文件行数: ${fileRows}`);

  return { ents: entArr.length, rules: ruleArr.length, actions: actArr.length, files: fileArr.length, withProps, withLE, withCode };
}

(async () => {
  console.log('='.repeat(55));
  console.log('  HR & 法律 v2 前端测试');
  console.log('='.repeat(55));

  const browser = await chromium.launch({ headless: false, slowMo: 40 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  try {
    // ── 登录 ────────────────────────────────────────────────────────
    console.log('\n[1] 登录...');
    await login(page);
    const tok = await getToken();

    // ── 本体列表 ────────────────────────────────────────────────────
    console.log('\n[2] 定位 v2 本体...');
    const hrOnt     = await findOntology(tok, 'HR本体-v2');
    const legalOnt  = await findOntology(tok, '法律本体-v2');

    if (!hrOnt)    { console.log('  ❌ HR本体-v2 未找到，请先运行 run_two_domains.py'); }
    if (!legalOnt) { console.log('  ❌ 法律本体-v2 未找到，请先运行 run_two_domains.py'); }

    await page.goto(`${BASE}/ontologies`);
    await wait(1500);
    await page.screenshot({ path: ss('ontology_list') });

    const results = {};

    // ── HR v2 ────────────────────────────────────────────────────────
    if (hrOnt) {
      console.log(`\n  HR v2: ${hrOnt.name} (${hrOnt.id})`);
      results.hr = await testOntologyDetail(page, tok, hrOnt.id, 'HR');
    }

    // ── 法律 v2 ─────────────────────────────────────────────────────
    if (legalOnt) {
      console.log(`\n  法律 v2: ${legalOnt.name} (${legalOnt.id})`);
      results.legal = await testOntologyDetail(page, tok, legalOnt.id, '法律');
    }

    // ── 总览 ─────────────────────────────────────────────────────────
    console.log('\n[3] 总览...');
    await page.goto(`${BASE}/overview`);
    await wait(2000);
    await page.screenshot({ path: ss('overview') });
    console.log('  ✓ 总览截图完成');

    // ── 汇总 ─────────────────────────────────────────────────────────
    console.log('\n\n' + '='.repeat(55));
    console.log('  v2 前端测试汇总');
    console.log('='.repeat(55));
    console.log(`${'领域'.padEnd(8)} ${'实体'.padStart(5)} ${'规则'.padStart(5)} ${'动作'.padStart(5)} ${'文件'.padStart(5)}`);
    console.log('-'.repeat(55));
    for (const [k, v] of Object.entries(results)) {
      console.log(`${k.padEnd(8)} ${String(v.ents).padStart(5)} ${String(v.rules).padStart(5)} ${String(v.actions).padStart(5)} ${String(v.files).padStart(5)}`);
    }
    console.log(`\n截图已保存至: ${SSDIR}`);

  } catch (err) {
    console.error('\n❌ 测试异常:', err.message);
    await page.screenshot({ path: ss('error') });
  } finally {
    await wait(2000);
    await browser.close();
  }
})();
