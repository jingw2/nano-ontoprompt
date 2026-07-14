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

const result = await page.evaluate(async () => {
  try {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'admin123' }),
    });
    const text = await res.text();
    return { status: res.status, text: text.substring(0, 500) };
  } catch (e) {
    return { error: e.message };
  }
});

console.log('API result:', JSON.stringify(result, null, 2));

await browser.close();
