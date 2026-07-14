import { chromium } from '@playwright/test';

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto('http://localhost:5173/login');
await page.waitForLoadState('networkidle');
await page.fill('input[placeholder="用户名"]', 'admin');
await page.fill('input[placeholder="密码"]', 'admin123');
await page.click('button[type="submit"]');
await new Promise(r => setTimeout(r, 3000));

console.log('URL after login:', page.url());

await page.goto('http://localhost:5173/ontologies');
await page.waitForLoadState('networkidle');
await new Promise(r => setTimeout(r, 2000));

console.log('URL on ontologies:', page.url());

const buttons = await page.locator('button').all();
console.log('\nAll buttons:');
for (const btn of buttons) {
  const text = await btn.innerText().catch(() => '');
  const visible = await btn.isVisible().catch(() => false);
  if (text.trim()) console.log('  [' + (visible ? 'V' : 'H') + '] "' + text.trim() + '"');
}

const body = await page.locator('body').innerText().catch(() => '');
console.log('\nPage text (first 500 chars):', body.substring(0, 500));

await browser.close();
