import { chromium } from '@playwright/test';
import http from 'http';

async function getToken() {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ username: 'admin', password: 'admin123' });
    const req = http.request({ hostname: 'localhost', port: 8000, path: '/api/v1/auth/login', method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } }, res => {
      let data = ''; res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data).data.access_token); } catch { reject(new Error(data)); } });
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

await page.goto('http://localhost:5173/ontologies/new');
await page.waitForLoadState('networkidle');
await new Promise(r => setTimeout(r, 3000));

console.log('URL:', page.url());
// Print page text
const body = await page.locator('body').innerText().catch(() => '');
console.log('Body text (first 500):', body.substring(0, 500));

await page.screenshot({ path: 'debug_wizard.png' });
console.log('Screenshot saved to debug_wizard.png');
await browser.close();
