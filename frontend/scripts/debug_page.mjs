import { chromium } from '@playwright/test';

const browser = await chromium.launch({
  headless: true,
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  args: ['--no-sandbox', '--disable-setuid-sandbox'],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

// 登录
await page.goto('http://localhost:5173/login');
await page.waitForLoadState('networkidle');
await page.fill('input[type="text"], input[placeholder*="用户"], input[placeholder*="user"]', 'admin');
await page.fill('input[type="password"]', 'admin123');
await page.click('button[type="submit"]');
await new Promise(r => setTimeout(r, 3000));

console.log('Current URL after login:', page.url());

await page.goto('http://localhost:5173/ontologies');
await page.waitForLoadState('networkidle');
await new Promise(r => setTimeout(r, 2000));

console.log('URL on ontologies page:', page.url());

// 所有按钮文字
const buttons = await page.locator('button').all();
console.log('\nAll buttons:');
for (const btn of buttons) {
  const text = await btn.innerText().catch(() => '');
  const visible = await btn.isVisible().catch(() => false);
  if (text.trim()) console.log(`  [${visible ? 'visible' : 'hidden'}] "${text.trim()}"`);
}

// 页面 h2/h1
const headings = await page.locator('h1, h2, h3').all();
console.log('\nHeadings:');
for (const h of headings) {
  console.log(`  "${await h.innerText().catch(() => '')}"`);
}

await browser.close();
