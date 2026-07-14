import { chromium } from '@playwright/test';

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.route('http://localhost:5173/api/**', async route => {
  const url = route.request().url().replace('http://localhost:5173', 'http://localhost:8000');
  try { const r = await route.fetch({ url }); await route.fulfill({ response: r }); } catch { await route.continue(); }
});

await page.goto('http://localhost:5173/login');
await page.waitForLoadState('networkidle');
await page.fill('input[placeholder="用户名"]', 'admin');
await page.fill('input[placeholder="密码"]', 'admin123');
await page.click('button[type="submit"]');
await new Promise(r => setTimeout(r, 3000));

// Check for Vite error overlay
const errorOverlay = page.locator('vite-error-overlay');
const hasError = await errorOverlay.isVisible({ timeout: 1000 }).catch(() => false);
console.log('Has Vite error overlay:', hasError);

await page.goto('http://localhost:5173/ontologies');
await page.waitForLoadState('networkidle');
await new Promise(r => setTimeout(r, 2000));

console.log('URL:', page.url());
const buttons = await page.locator('button').all();
console.log('Buttons:');
for (const b of buttons) {
  const txt = await b.innerText().catch(() => '');
  const vis = await b.isVisible().catch(() => false);
  if (txt.trim()) console.log('  [' + (vis ? 'V' : 'H') + ']', JSON.stringify(txt.trim()));
}

// Check for error message in page
const body = await page.locator('body').innerText().catch(() => '');
if (body.includes('error') || body.includes('Error')) {
  console.log('Page body (first 300):', body.substring(0, 300));
}

await browser.close();
