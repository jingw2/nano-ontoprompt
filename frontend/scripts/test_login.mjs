import { chromium } from '@playwright/test';

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe',
  args: ['--no-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

for (const pwd of ['admin123', 'admin123', 'admin', 'password', 'ontoprompt']) {
  await page.goto('http://localhost:5173/login');
  await page.waitForLoadState('networkidle');
  await page.fill('input[type="text"]', 'admin');
  await page.fill('input[type="password"]', pwd);
  await page.click('button[type="submit"]');
  await new Promise(r => setTimeout(r, 2000));
  const url = page.url();
  console.log('password:', pwd, '-> URL:', url);
  if (!url.includes('/login')) { console.log('SUCCESS with:', pwd); break; }
}

await browser.close();
