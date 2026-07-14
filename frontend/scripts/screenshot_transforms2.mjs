import { chromium } from '@playwright/test';
import fs from 'fs'; import path from 'path'; import http from 'http';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SS = path.join(__dirname, 'sc_fulltest_screenshots');

async function getToken() {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ username: 'admin', password: 'admin123' });
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v1/auth/login', method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, res => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d).data.access_token); } catch { reject(new Error(d)); } });
    });
    req.on('error', reject); req.write(body); req.end();
  });
}

const token = await getToken();
const browser = await chromium.launch({ headless: true, executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(t => {
  localStorage.setItem('auth-store', JSON.stringify({ state: { user: { id: 'a', username: 'admin', role: 'admin' }, token: t }, version: 0 }));
  localStorage.setItem('token', t); localStorage.setItem('lang', 'zh');
}, token);
await ctx.route('http://localhost:5173/api/**', async route => {
  const url = route.request().url().replace('http://localhost:5173', 'http://localhost:8000');
  try { const r = await route.fetch({ url }); await route.fulfill({ response: r }); } catch { await route.continue(); }
});
const page = await ctx.newPage();

await page.goto('http://localhost:5173/pipelines/transforms');
await page.waitForLoadState('networkidle');
await new Promise(r => setTimeout(r, 2000));

// 点第一个流水线的展开按钮（最右侧按钮）
const cards = await page.locator('div.border.rounded-xl').all();
if (cards.length > 0) {
  const expandBtn = cards[0].locator('button').last();
  await expandBtn.click().catch(() => {});
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: path.join(SS, 'T3_route_a_expanded.png'), fullPage: false });
  console.log('T3_route_a_expanded.png');
}

// 点第三个流水线（Route B）
if (cards.length > 2) {
  const expandBtn2 = cards[2].locator('button').last();
  await expandBtn2.click().catch(() => {});
  await new Promise(r => setTimeout(r, 2000));
  await page.screenshot({ path: path.join(SS, 'T4_route_b_expanded.png'), fullPage: false });
  console.log('T4_route_b_expanded.png');
}

// 全部展开截图
for (const card of cards) {
  const lastBtn = card.locator('button').last();
  await lastBtn.click().catch(() => {});
  await new Promise(r => setTimeout(r, 300));
}
await new Promise(r => setTimeout(r, 2000));
await page.screenshot({ path: path.join(SS, 'T5_all_expanded.png'), fullPage: true });
console.log('T5_all_expanded.png');

await browser.close();
