/**
 * ui.ts — I-FRONTEND e2e helpers: UI login (F0 in-memory bearer) + agent row
 * navigation.  The access token lives only in Zustand memory, so E2E auth is
 * a real UI login (cookie-based refresh, single-use per family).
 *
 * The login endpoint is slowapi-limited (5/minute per IP): logins are paced
 * ~13s apart and retried with backoff on 429, exactly like the reviewer's
 * e2e-review helpers.
 */
import type { Page } from '@playwright/test'

export const AGENT_E2E_ADMIN_USER = process.env.AGENT_E2E_ADMIN_USER || 'admin'
export const AGENT_E2E_ADMIN_PASSWORD = process.env.AGENT_E2E_ADMIN_PASSWORD || 'admin123'

let _lastLoginAt = Date.now()

export async function loginAsAdmin(page: Page): Promise<void> {
  const elapsed = Date.now() - _lastLoginAt
  if (_lastLoginAt !== 0 && elapsed < 13_000) {
    await page.waitForTimeout(13_000 - elapsed)
  }
  for (let attempt = 0; attempt < 5; attempt++) {
    if (attempt > 0) await page.waitForTimeout(20_000 + attempt * 15_000)
    await page.goto('/login')
    await page.locator('input[placeholder="用户名"], input[placeholder="Username"]').first().fill(AGENT_E2E_ADMIN_USER)
    await page.locator('input[placeholder="密码"], input[placeholder="Password"]').first().fill(AGENT_E2E_ADMIN_PASSWORD)
    await page.locator('button[type="submit"], button:has-text("登录"), button:has-text("Login")').first().click()
    try {
      await page.waitForURL(/\/overview|\/agents/, { timeout: 20_000 })
      _lastLoginAt = Date.now()
      return
    } catch {
      // still on /login — likely rate-limited; retry after a longer backoff
    }
  }
  throw new Error(`loginAsAdmin failed after retries (URL ${page.url()})`)
}

/** Open the first agent row from /agents; returns false when none exists. */
export async function openFirstAgent(page: Page): Promise<boolean> {
  await page.goto('/agents')
  const name = page.locator('tbody tr button').first()
  try {
    await name.waitFor({ state: 'visible', timeout: 15_000 })
  } catch {
    return false
  }
  await name.click()
  await page.waitForURL(/\/agents\/[^/]+$/, { timeout: 30_000 })
  return true
}
