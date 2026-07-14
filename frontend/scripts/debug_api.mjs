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

// 在浏览器内测试 API
const result = await page.evaluate(async () => {
  try {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'admin123' }),
    });
    const json = await res.json();
    return { status: res.status, body: json };
  } catch (e) {
    return { error: e.message };
  }
});

console.log('API test result:', JSON.stringify(result, null, 2));

await browser.close();
