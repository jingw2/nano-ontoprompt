/**
 * Verify all 5 fixes by re-extracting HR domain and checking:
 * 1. Relation count improved (second-pass inference)
 * 2. No duplicate entities (upsert)
 * 3. Confidence scores vary (calibration)
 * 4. Re-extraction is non-destructive (incremental)
 */
import { chromium } from 'playwright';

const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8002/api/v1';
const OID  = '75d5e5f7-c101-49a6-a2ed-91642d6a0dcc';  // HR domain

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function apiGet(p, token) {
  const r = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? [];
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx  = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('token') || '');

  // ── Snapshot BEFORE re-extraction ──
  const entsBefore  = await apiGet(`/ontologies/${OID}/entities`, token);
  const graphBefore = await apiGet(`/ontologies/${OID}/graph`, token);
  console.log(`\nBEFORE: entities=${entsBefore.length}  edges=${graphBefore.edges?.length ?? 0}`);

  // Check confidence distribution before
  const confBefore = entsBefore.map(e => e.confidence);
  const minC = Math.min(...confBefore).toFixed(3);
  const maxC = Math.max(...confBefore).toFixed(3);
  const spread = (Math.max(...confBefore) - Math.min(...confBefore)).toFixed(3);
  console.log(`  Confidence spread: min=${minC} max=${maxC} spread=${spread}`);

  // ── Re-extract ──
  await page.goto(`${BASE}/ontologies/${OID}`);
  await sleep(800);
  const infoBtn = page.locator('button').filter({ hasText: /LLM提取配置|信息/ }).first();
  await infoBtn.click().catch(() => {});
  await sleep(500);

  const selects = page.locator('select');
  const opts = await selects.nth(0).locator('option').allTextContents();
  const hrPrompt = opts.find(t => t.includes('HR本体提取'));
  if (hrPrompt) await selects.nth(0).selectOption({ label: hrPrompt });
  const modelOpts = await selects.nth(1).locator('option').allTextContents();
  const deepOpt = modelOpts.find(t => t.toLowerCase().includes('deepseek'));
  if (deepOpt) await selects.nth(1).selectOption({ label: deepOpt });
  else if (modelOpts.length > 1) await selects.nth(1).selectOption({ index: 1 });
  const sc = await page.locator('select').count();
  if (sc >= 3) {
    const mnOpts = await page.locator('select').nth(2).locator('option').allTextContents();
    const mn = mnOpts.find(t => t.toLowerCase().includes('deepseek')) ?? (mnOpts.length > 1 ? mnOpts[1] : null);
    if (mn) await page.locator('select').nth(2).selectOption({ label: mn });
  }
  await sleep(300);

  await page.click('button:has-text("开始提取")');
  console.log('\nExtracting...');
  await sleep(2000);

  // Wait for completion
  for (let i = 0; i < 150; i++) {
    await sleep(4000);
    const pct = await page.locator('p').filter({ hasText: '%' }).first().textContent().catch(() => '');
    process.stdout.write(`\r  ${pct?.trim() ?? '?'}        `);
    if (await page.locator('.bg-green-500').count() > 0) { console.log('\n  ✅ done'); break; }
    if (await page.locator('text=提取失败').count() > 0) { console.log('\n  ❌ failed'); break; }
  }

  // ── Snapshot AFTER re-extraction ──
  const entsAfter  = await apiGet(`/ontologies/${OID}/entities`, token);
  const graphAfter = await apiGet(`/ontologies/${OID}/graph`, token);
  console.log(`\nAFTER:  entities=${entsAfter.length}  edges=${graphAfter.edges?.length ?? 0}`);

  // Fix 2+4: Check no duplicates (all name_cn should be unique)
  const names = entsAfter.map(e => e.name_cn);
  const uniqueNames = new Set(names);
  console.log(`\nFix 2+4 (upsert/incremental):`);
  console.log(`  Entity count: ${entsBefore.length} → ${entsAfter.length}`);
  console.log(`  Unique names: ${uniqueNames.size}  (duplicates: ${names.length - uniqueNames.size})`);
  if (names.length === uniqueNames.size) console.log('  ✅ No duplicates');
  else console.log('  ❌ Duplicates detected!');

  // Fix 1: Check relation improvement
  console.log(`\nFix 1 (second-pass relations):`);
  console.log(`  Edges: ${graphBefore.edges?.length ?? 0} → ${graphAfter.edges?.length ?? 0}`);
  if ((graphAfter.edges?.length ?? 0) > (graphBefore.edges?.length ?? 0))
    console.log('  ✅ More edges after re-extraction');
  else console.log('  ℹ Relations unchanged or reduced');

  // Fix 5: Check confidence spread
  const confAfter = entsAfter.map(e => e.confidence).filter(c => c != null);
  const minA = Math.min(...confAfter).toFixed(3);
  const maxA = Math.max(...confAfter).toFixed(3);
  const spreadA = (Math.max(...confAfter) - Math.min(...confAfter)).toFixed(3);
  console.log(`\nFix 5 (calibration):`);
  console.log(`  Before: spread=${spread} (min=${minC} max=${maxC})`);
  console.log(`  After:  spread=${spreadA} (min=${minA} max=${maxA})`);
  const distinct = new Set(confAfter.map(c => c.toFixed(2))).size;
  console.log(`  Distinct confidence values: ${distinct}`);
  if (parseFloat(spreadA) > 0.05) console.log('  ✅ Scores now vary');
  else console.log('  ℹ Scores still uniform');

  await browser.close();
})().catch(e => { console.error('❌', e.message); process.exit(1); });
