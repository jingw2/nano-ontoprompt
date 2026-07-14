import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import http from 'http';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SS = path.join(__dirname, 'sc_fulltest_screenshots');
fs.mkdirSync(SS, { recursive: true });

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

// 展开所有 pipeline（点击向下箭头）
const chevrons = page.locator('button:has(svg)').filter({ hasText: '' });
const expandBtns = await page.locator('svg').all();

// 点击每个 pipeline 的展开按钮（最后一列的按钮）
const allBtns = await page.locator('div.border.rounded-xl > div > div > button').all();
for (const btn of allBtns) {
  const txt = await btn.innerText().catch(() => '');
  if (!txt.includes('运行')) {
    await btn.click().catch(() => {});
    await new Promise(r => setTimeout(r, 500));
  }
}
await new Promise(r => setTimeout(r, 1500));
await page.screenshot({ path: path.join(SS, 'T1_transforms_expanded.png'), fullPage: true });
console.log('Screenshot: T1_transforms_expanded.png');

// 点第一个 pipeline 展开 (ChevronDown)
const chevronBtns = await page.locator('button').filter({ has: page.locator('svg') }).all();
// 依次点一次前4个"非运行"按钮
for (let i = 0; i < Math.min(4, chevronBtns.length); i++) {
  const txt = await chevronBtns[i].innerText().catch(() => '');
  if (!txt.trim() || txt === '') {
    await chevronBtns[i].click().catch(() => {});
    await new Promise(r => setTimeout(r, 800));
  }
}
await new Promise(r => setTimeout(r, 2000));
await page.screenshot({ path: path.join(SS, 'T2_transforms_steps.png'), fullPage: true });
console.log('Screenshot: T2_transforms_steps.png');

await browser.close();
