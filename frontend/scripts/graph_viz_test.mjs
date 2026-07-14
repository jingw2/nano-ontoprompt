/**
 * Test improved graph visualization on existing multi-domain ontologies
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = 'http://localhost:5173';
const API  = 'http://localhost:8002/api/v1';
const SSDIR = 'E:/零点未来/137. nanoontology/ontoprompt/frontend/screenshots_graph_improved';
mkdirSync(SSDIR, { recursive: true });

let step = 0;
const ss = (label) => `${SSDIR}/${String(++step).padStart(3,'0')}_${label.replace(/[^\w一-龥]/g,'_').slice(0,40)}.png`;
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function apiGet(p, token) {
  const r = await fetch(`${API}${p}`, { headers: { Authorization: `Bearer ${token}` } });
  return (await r.json()).data ?? [];
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 30 });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`);
  await page.fill('input[placeholder="用户名"]', 'admin');
  await page.fill('input[placeholder="密码"]', 'admin123');
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/overview`, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('token') || '');
  console.log('✓ 已登录');

  const targets = [
    { id: 'ee16adcf-ca0d-44b8-8698-67c7e8242dec', name: '财务' },
    { id: 'f3fa7adb-9ad0-44a9-aa98-b6869f315242', name: '医疗' },
    { id: '5234b7f3-2e3d-4536-b2ce-f48508b596a4', name: '营销' },
    { id: '75d5e5f7-c101-49a6-a2ed-91642d6a0dcc', name: 'HR' },
  ];

  for (const { id, name } of targets) {
    const graph = await apiGet(`/ontologies/${id}/graph`, token);
    const nodeCount = graph.nodes?.length ?? 0;
    const edgeCount = graph.edges?.length ?? 0;
    const isolated = (graph.nodes || []).filter(n => {
      const deg = (graph.edges || []).filter(e => e.data.source === n.data.id || e.data.target === n.data.id).length;
      return deg === 0;
    }).length;
    console.log(`\n[${name}] 节点:${nodeCount} 边:${edgeCount} 孤立:${isolated}`);

    await page.goto(`${BASE}/ontologies/${id}`);
    await sleep(1200);
    
    const tabBtn = page.locator('button').filter({ hasText: '知识图谱' });
    if (await tabBtn.count() > 0) {
      await tabBtn.first().click();
      await sleep(3000);
      await page.screenshot({ path: ss(`${name}_力导向图`) });
      console.log(`  ✓ 力导向截图`);

      const bfBtn = page.locator('button').filter({ hasText: '层级' });
      if (await bfBtn.count() > 0) {
        await bfBtn.first().click();
        await sleep(2000);
        await page.screenshot({ path: ss(`${name}_层级图`) });
        console.log(`  ✓ 层级截图`);
      }
    }
  }

  console.log(`\n📸 共 ${step} 张截图 → ${SSDIR}`);
  await browser.close();
})().catch(e => { console.error('❌', e.message); process.exit(1); });
